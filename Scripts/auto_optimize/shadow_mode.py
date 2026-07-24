"""Layer C shadow mode: 新 policy 部署前先 shadow (只记录预测 alpha 不下单).

按 docs/self-update-youhua.md 重大纰漏 #4:
- 新 policy 与 champion 并行跑 N 天, 只记录预测动作, 不真实影响下单
- 新旧 policy 动作分布 KL 散度作第六道门, 防止重训后行为剧变未拦住

实现: 一个 shadow inference_server, 收到 state 后同时算 champion 和 challenger 的 alpha,
记录二者到日志, 但只把 champion 的 alpha 返回给 RlRiskModel (不影响实盘).
"""
import json, pathlib, argparse, threading
import zmq, onnxruntime, numpy as np
from manifest_loader import load_manifest


class ShadowInferenceServer:
    """Shadow server: 同时跑 champion + challenger, 记录对比, 返回 champion alpha."""

    def __init__(self, manifest, champion_onnx, challenger_onnx, endpoint,
                 log_path, kl_window=100):
        self.manifest = manifest
        self.champion = onnxruntime.InferenceSession(champion_onnx)
        self.challenger = onnxruntime.InferenceSession(challenger_onnx)
        self.champ_input = self.champion.get_inputs()[0].name
        self.chall_input = self.challenger.get_inputs()[0].name
        self.socket = zmq.Context().socket(zmq.REP)
        self.socket.bind(endpoint)
        self.log_path = pathlib.Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.kl_window = kl_window
        self._champ_alphas = []
        self._chall_alphas = []
        self._lock = threading.Lock()
        self._n_requests = 0

    def _encode(self, state_json):
        s = json.loads(state_json)
        return np.array([[s["tpv"], s["cash_pct"], s["var_1d99"], s["var_regime"],
                          s["drawdown"], s["n_open_positions"], s.get("pnl", 0), 0]],
                         dtype=np.float32)

    @staticmethod
    def _kl_divergence(p: np.ndarray, q: np.ndarray, n_bins: int = 10) -> float:
        """两 alpha 分布的 KL 散度 (直方图近似). p=champion, q=challenger."""
        bins = np.linspace(0, 1, n_bins + 1)
        ph, _ = np.histogram(p, bins=bins, density=False)
        qh, _ = np.histogram(q, bins=bins, density=False)
        ph = ph / max(ph.sum(), 1)
        qh = qh / max(qh.sum(), 1)
        eps = 1e-10
        return float(np.sum(ph * np.log((ph + eps) / (qh + eps))))

    def serve_forever(self):
        while True:
            state_json = self.socket.recv_string()
            try:
                state_vec = self._encode(state_json)
                champ_alpha = float(self.champion.run(None, {self.champ_input: state_vec})[0][0][0])
                chall_alpha = float(self.challenger.run(None, {self.chall_input: state_vec})[0][0][0])
                champ_alpha = max(0.0, min(1.0, champ_alpha))
                chall_alpha = max(0.0, min(1.0, chall_alpha))

                with self._lock:
                    self._champ_alphas.append(champ_alpha)
                    self._chall_alphas.append(chall_alpha)
                    if len(self._champ_alphas) > self.kl_window:
                        self._champ_alphas.pop(0)
                        self._chall_alphas.pop(0)
                    self._n_requests += 1
                    if self._n_requests % self.kl_window == 0 and len(self._champ_alphas) >= self.kl_window:
                        kl = self._kl_divergence(np.array(self._champ_alphas),
                                                 np.array(self._chall_alphas))
                        mean_diff = float(np.mean(np.array(self._chall_alphas) - np.array(self._champ_alphas)))
                        self._write_log(self._n_requests, kl, mean_diff, champ_alpha, chall_alpha)
                        if kl > 0.1:
                            print(f"[shadow] ⚠️ KL={kl:.4f} > 0.1, challenger 行为剧变, 不建议部署")

                self.socket.send_string(json.dumps({
                    "action": "scale", "alpha": champ_alpha,
                    "shadow_challenger_alpha": chall_alpha,
                }))
            except Exception as e:
                self.socket.send_string(json.dumps({"action": "scale", "alpha": 0.5, "error": str(e)}))

    def _write_log(self, n_req, kl, mean_diff, champ_a, chall_a):
        with self.log_path.open("a") as f:
            f.write(json.dumps({"n_requests": n_req, "kl_divergence": kl,
                                "mean_diff_challenger_minus_champion": mean_diff,
                                "latest_champion_alpha": champ_a,
                                "latest_challenger_alpha": chall_a}) + "\n")


def run_server(manifest_path, champion_onnx, challenger_onnx, endpoint, log_path):
    manifest = load_manifest(manifest_path)
    server = ShadowInferenceServer(manifest, champion_onnx, challenger_onnx, endpoint, log_path)
    server.serve_forever()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--champion-onnx", required=True, help="当前部署的 policy ONNX")
    ap.add_argument("--challenger-onnx", required=True, help="待验证的新 policy ONNX")
    ap.add_argument("--endpoint", default="tcp://127.0.0.1:5558")
    ap.add_argument("--log-path", default="Results/auto_optimize/option_vol_arb_5layer/shadow_kl.jsonl")
    ap.add_argument("--kl-window", type=int, default=100)
    args = ap.parse_args()
    run_server(args.manifest, args.champion_onnx, args.challenger_onnx,
               args.endpoint, args.log_path)
