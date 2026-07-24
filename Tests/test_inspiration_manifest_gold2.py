"""Tests for gold2 manifest inspiration: block. Spec §2.5."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from manifest_loader import load_manifest  # noqa: E402

MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"


def test_manifest_has_inspiration_block():
    m = load_manifest(MANIFEST)
    insp = m.raw.get("inspiration", {})
    assert insp, "inspiration: block missing"
    assert insp["persistence"]["min_generations"] == 3
    assert insp["persistence"]["gap_threshold"] == 0.15
    assert insp["persistence"]["weight_nonconvergence_delta"] == 0.1
    assert insp["timeout_generations"] == 5
    assert insp["llm"]["config_key"] == "code-generation-llm"
    assert insp["llm"]["model"] == "glm-5.1"
