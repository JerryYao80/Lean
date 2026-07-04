"""Layer C deploy: ZeroMQ REP + ONNX forward, frozen weights. Spec §6.5, §6.7."""
import json, argparse, threading
import zmq, onnxruntime, numpy as np
from manifest_loader import load_manifest

class InferenceServer:
    def __init__(self, manifest, onnx_path, endpoint):
        self.manifest = manifest
        self.session = onnxruntime.InferenceSession(onnx_path)
        self.input_name = self.session.get_inputs()[0].name
        self.socket = zmq.Context().socket(zmq.REP)
        self.socket.bind(endpoint)

    def _encode(self, state_json):
        s = json.loads(state_json)
        # 按 manifest.state_schema 字段顺序编码 (简化: 用固定 8 维)
        return np.array([[s["tpv"], s["cash_pct"], s["var_1d99"], s["var_regime"],
                          s["drawdown"], s["n_open_positions"], s.get("pnl", 0), 0]],
                         dtype=np.float32)

    def _decode(self, action_vec):
        alpha = float(action_vec[0][0])
        return {"action": "scale", "alpha": max(0.0, min(1.0, alpha)), "per_symbol_override": None}

    def serve_forever(self):
        while True:
            state_json = self.socket.recv_string()
            try:
                state_vec = self._encode(state_json)
                action_vec = self.session.run(None, {self.input_name: state_vec})[0]
                self.socket.send_string(json.dumps(self._decode(action_vec)))
            except Exception as e:
                self.socket.send_string(json.dumps({"action": "scale", "alpha": 0.5, "error": str(e)}))

def run_server(manifest_path, onnx_path, endpoint):
    manifest = load_manifest(manifest_path)
    server = InferenceServer(manifest, onnx_path, endpoint)
    server.serve_forever()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--endpoint", default="tcp://127.0.0.1:5555")
    args = ap.parse_args()
    run_server(args.manifest, args.onnx, args.endpoint)
