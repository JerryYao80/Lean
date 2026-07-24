"""Tests for generational log. Spec §2.2."""
import json, sys, tempfile
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
from generations import log, read_history  # noqa: E402

def test_log_writes_generation_file():
    with tempfile.TemporaryDirectory() as d:
        log("Gold2", 3, {"extreme_risk": {"pnl_pct_of_total": -0.32, "gap": 0.32}},
            {"extreme_risk_contrib_penalty": 2.13}, "pass", repo_root=d)
        gen_file = Path(d) / "Results" / "auto_optimize" / "Gold2" / "generation_3.json"
        assert gen_file.exists()
        doc = json.loads(gen_file.read_text())
        assert doc["strategy"] == "Gold2"
        assert doc["generation"] == 3
        assert doc["layer_gaps"]["extreme_risk"]["gap"] == 0.32
        assert doc["shaping_overrides"]["extreme_risk_contrib_penalty"] == 2.13

def test_read_history_returns_last_n():
    with tempfile.TemporaryDirectory() as d:
        for n in [1, 2, 3]:
            log("Gold2", n, {"extreme_risk": {"gap": 0.3 + n * 0.01}},
                {"extreme_risk_contrib_penalty": 1.0 + n}, "pass", repo_root=d)
        history = read_history("Gold2", 2, repo_root=d)
        assert len(history) == 2

def test_read_history_returns_all_when_fewer_than_n():
    with tempfile.TemporaryDirectory() as d:
        log("Gold2", 1, {"extreme_risk": {"gap": 0.32}}, {}, "pass", repo_root=d)
        history = read_history("Gold2", 3, repo_root=d)
        assert len(history) == 1

def test_read_history_empty_when_no_files():
    with tempfile.TemporaryDirectory() as d:
        history = read_history("Gold2", 3, repo_root=d)
        assert history == []
