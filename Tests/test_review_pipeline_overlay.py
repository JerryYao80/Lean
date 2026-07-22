"""Tests for pipeline overlay + manifest_lint warnings. Spec §1.5, §5.3, §5.4."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from pipeline_overlay import should_run  # noqa: E402
from manifest_loader import load_manifest  # noqa: E402
from manifest_lint import lint_manifest  # noqa: E402

MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"


def test_should_run_manual_returns_false():
    m = load_manifest(MANIFEST)
    m.raw["review"]["review_schedule"] = "manual"
    assert should_run(m) is False


def test_should_run_on_backtest_returns_true():
    m = load_manifest(MANIFEST)
    m.raw["review"]["review_schedule"] = "on_backtest"
    assert should_run(m) is True


def test_should_run_no_review_block_returns_false():
    class FakeM:
        raw = {}
    assert should_run(FakeM()) is False


def test_manifest_lint_has_warnings_field():
    """Spec §5.4: LintResult gains warnings list; review-not-run → warning, not error."""
    m = load_manifest(MANIFEST)
    result = lint_manifest(m, code_params={"trend-disable"}, state_fields={"ts"})
    assert hasattr(result, "warnings")
    assert isinstance(result.warnings, list)
    assert result.ok == (len(result.errors) == 0)
