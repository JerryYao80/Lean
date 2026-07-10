"""Tests for gold2 manifest review: block + trend-disable + state_schema. Spec §5.1, §3.3."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from manifest_loader import load_manifest  # noqa: E402

MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"


def test_manifest_has_review_block():
    m = load_manifest(MANIFEST)
    r = m.raw.get("review", {})
    assert r, "review: block missing"
    assert r["adapter_module"] == "adapters.gold2"
    assert r["adapter_class"] == "Gold2ReviewAdapter"
    assert r["layer_names"] == ["trend", "vol_target", "extreme_risk", "realrate_cap"]
    assert r.get("block_deploy") is False
    assert "thresholds" in r


def test_trend_disable_in_parameter_space():
    m = load_manifest(MANIFEST)
    names = [p.name for p in m.parameter_space]
    assert "trend-disable" in names, "trend-disable missing from parameter_space (strategy GetTunableParameterNames has it)"


def test_state_schema_has_4_review_fields():
    m = load_manifest(MANIFEST)
    names = {f.name for f in m.state_schema.fields}
    for f in ["dir_coef", "w_after_vol", "extreme_cap", "trend_disabled"]:
        assert f in names, f"state field {f} missing (SerializeRlState emits it)"
    assert m.state_schema.dim_hint == 13
