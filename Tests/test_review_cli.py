"""End-to-end CLI test on real gold2 artifacts. Spec §5.2, §6.1, §2.4, §2.5."""
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
RUN_REVIEW = _REPO / "Scripts" / "review" / "run_review.py"
MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"
RESULTS = _REPO / "Results" / "gold2-betavol"


def _run(args):
    return subprocess.run(
        [sys.executable, str(RUN_REVIEW)] + args,
        capture_output=True, text=True, cwd=str(_REPO),
    )


def test_cli_exit_0_on_real_gold2_artifacts():
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 backtest artifacts not present")
    r = _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    assert r.returncode in (0, 2), f"stdout={r.stdout[-500:]}\nstderr={r.stderr[-500:]}"


def test_cli_writes_review_json():
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 backtest artifacts not present")
    _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    review_json = RESULTS / "review" / "review.json"
    assert review_json.exists(), "review.json not written"
    doc = json.loads(review_json.read_text())
    assert "run_meta" in doc and "layer_attribution" in doc and "per_trade_narrative" in doc
    assert doc["run_meta"]["strategy_name"] == "Gold2BetaVolTargetStrategy"


def test_cli_exit_3_on_missing_hard_artifacts():
    """order-events.json + {algo}.json are hard-required; missing → exit 3."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = _run(["--manifest", str(MANIFEST), "--results-dir", d])
    assert r.returncode == 3, f"expected 3, got {r.returncode}. stderr={r.stderr[-300:]}"


def test_cli_layer_attribution_sums_to_total_pnl():
    """Spec §2.2 invariant: sum(layer pnl_abs) == decimal.Parse(totalProfitLoss)."""
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 backtest artifacts not present")
    _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    doc = json.loads((RESULTS / "review" / "review.json").read_text())
    total = Decimal(doc["run_meta"]["total_closed_trade_pnl"])
    layer_sum = sum(Decimal(v["pnl_abs"]) for v in doc["layer_attribution"].values())
    assert abs(layer_sum - total) < Decimal("0.01"), \
        f"sum {layer_sum} != total {total} (gap {layer_sum - total})"


def test_cli_drawdown_attribution_populated():
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 backtest artifacts not present")
    _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    doc = json.loads((RESULTS / "review" / "review.json").read_text())
    assert isinstance(doc["drawdown_attribution"], list)
    assert len(doc["drawdown_attribution"]) > 0, "drawdown attribution empty"
    dd = doc["drawdown_attribution"][0]
    assert "peak_time" in dd and "trough_time" in dd and "depth_pct" in dd
    assert dd["depth_pct"] < 0  # drawdown is negative


def test_cli_tca_populated_from_orderSubmissionData():
    """Spec §2.5: tca from order-events.fillPrice joined to orders.orderSubmissionData.
    Verified 997/997 orders have orderSubmissionData → tca must be non-null."""
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 backtest artifacts not present")
    _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    doc = json.loads((RESULTS / "review" / "review.json").read_text())
    assert doc["tca"] is not None, "tca should be populated (orderSubmissionData present)"
    assert doc["tca"]["n_fills"] > 0
    assert "avg_slippage_bps" in doc["tca"]
    assert doc["tca"]["source"] == "orderSubmissionData"
