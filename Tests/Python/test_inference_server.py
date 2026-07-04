import json, threading, time, zmq, numpy as np, pytest
from unittest.mock import patch
from inference_server import InferenceServer

class FakeSession:
    def __init__(self): self.input_name = "state"
    def run(self, _, feeds):
        return [np.array([[0.6]])]  # alpha=0.6

def test_roundtrip(tmp_path):
    # 用 fake session 启动 server
    endpoint = "tcp://127.0.0.1:59998"
    class FakeServer(InferenceServer):
        def __init__(self, manifest, onnx_path, endpoint):
            self.manifest = manifest
            self.session = FakeSession()
            self.input_name = "state"
            self.socket = zmq.Context().socket(zmq.REP)
            self.socket.bind(endpoint)
    from manifest_loader import load_manifest
    import pathlib
    m = load_manifest(pathlib.Path(__file__).parent / "fixtures" / "manifest_sample.yaml")
    server = FakeServer(m, None, endpoint)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    req = zmq.Context().socket(zmq.REQ)
    req.connect(endpoint)
    req.send_string(json.dumps({"tpv":1e6,"cash_pct":0.5,"var_1d99":0.02,"var_regime":0.6,
                                "drawdown":0.01,"n_open_positions":1,"pnl":0.01}))
    reply = json.loads(req.recv_string())
    assert 0 <= reply["alpha"] <= 1
    assert reply["action"] == "scale"
