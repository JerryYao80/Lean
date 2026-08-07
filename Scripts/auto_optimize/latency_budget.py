"""延迟预算测量 (auto-update3.md 第3点).

测量 C# ZeroMQ 请求 → Python inference → 返回的 round-trip 延迟分布.
用真实 ONNX policy + 真实 trace 状态做 N 次 replay, 报告 p50/p95/p99.
"""
import sys, json, pathlib, argparse, time
import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "Scripts" / "auto_optimize"))

import zmq, onnxruntime


def load_trace_states(trace_path):
    states = []
    for line in pathlib.Path(trace_path).read_text().splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        states.append([
            s["tpv"], s["cash_pct"], s["var_1d99"], s["var_regime"],
            s["drawdown"], s["n_open_positions"], s.get("pnl", 0), 0
        ])
    return np.array(states, dtype=np.float32)


def measure_round_trip(onnx_path, trace_path, n_iterations=1000):
    """直接调 ONNX (无 ZeroMQ), 测纯推理延迟 + 模拟 ZeroMQ round-trip."""
    states = load_trace_states(trace_path)
    if len(states) == 0:
        return {"error": "empty trace"}

    sess = onnxruntime.InferenceSession(onnx_path)
    input_name = sess.get_inputs()[0].name

    inference_latencies = []
    for i in range(n_iterations):
        state = states[i % len(states)].reshape(1, -1)
        t0 = time.perf_counter()
        sess.run(None, {input_name: state})
        inference_latencies.append((time.perf_counter() - t0) * 1000)

    rt_latencies = []
    ctx = zmq.Context()
    rep = ctx.socket(zmq.REP)
    rep.bind("inproc://latency-test")
    req = ctx.socket(zmq.REQ)
    req.connect("inproc://latency-test")

    for i in range(min(n_iterations, 200)):
        state_json = json.dumps({"tpv": float(states[i % len(states)][0]),
                                 "cash_pct": 0.5, "var_1d99": 0.02,
                                 "var_regime": 0.5, "drawdown": 0.01,
                                 "n_open_positions": 1, "pnl": 0.01})
        t0 = time.perf_counter()
        req.send_string(state_json)
        msg = rep.recv_string()
        state_vec = states[i % len(states)].reshape(1, -1)
        sess.run(None, {input_name: state_vec})
        rep.send_string(json.dumps({"alpha": 0.6}))
        reply = req.recv_string()
        rt_latencies.append((time.perf_counter() - t0) * 1000)

    inf = np.array(inference_latencies)
    rt = np.array(rt_latencies)

    return {
        "inference_ms": {
            "p50": float(np.percentile(inf, 50)),
            "p95": float(np.percentile(inf, 95)),
            "p99": float(np.percentile(inf, 99)),
            "max": float(inf.max()),
            "mean": float(inf.mean()),
        },
        "round_trip_ms": {
            "p50": float(np.percentile(rt, 50)),
            "p95": float(np.percentile(rt, 95)),
            "p99": float(np.percentile(rt, 99)),
            "max": float(rt.max()),
            "mean": float(rt.mean()),
        },
        "n_inference": len(inf),
        "n_round_trip": len(rt),
        "budget_50ms": bool(np.percentile(rt, 99) < 50),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--n", type=int, default=1000)
    args = ap.parse_args()
    result = measure_round_trip(args.onnx, args.trace, args.n)
    print(json.dumps(result, indent=2))
