"""G3-Real LLM client: glm-5.2 via mydamoxing.cn, no production import, no blind leakage."""
import inspect, sys, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# The prompt-source files embed full strategy + risk-model C#; we only care
# that the G3-Real module's OWN text (not the embedded C#) avoids the
# forbidden production imports and out-of-sample year tokens.
SRC_PATH = ROOT / "Scripts/gold2_closed_loop/g3_real_llm_client.py"

def test_module_does_not_import_production():
    src = SRC_PATH.read_text()
    for forbidden in ("soloquant_orchestrator", "evolution_scheduler", "layer_state",
                      "inspiration", "generation_state"):
        assert re.search(rf"\bimport\s+{re.escape(forbidden)}\b", src) is None, \
            f"must not import {forbidden}"
        assert re.search(rf"\bfrom\s+{re.escape(forbidden)}\b", src) is None, \
            f"must not import {forbidden}"

def test_http_shape_mirrors_openai_compatible():
    src = SRC_PATH.read_text()
    assert "mydamoxing.cn" in src
    assert "/v1/chat/completions" in src
    assert "Bearer" in src
    assert "glm-5.2" in src

def test_build_prompt_has_no_blind_leakage():
    from Scripts.gold2_closed_loop.g3_real_llm_client import build_prompt
    synthetic_bundle = {
        "window_id": "W1", "validity_status": "VALID",
        "layer_attribution": {
            "trend": {"pnl_pct_of_total": "0.45"},
            "vol_target": {"pnl_pct_of_total": "0.30"},
            "extreme_risk": {"pnl_pct_of_total": "0.10"},
            "realrate_cap": {"pnl_pct_of_total": "0.15"},
        },
    }
    prompt = build_prompt(synthetic_bundle, instrument="518880")
    # The prompt must not name specific out-of-sample years or use the word
    # "blind" — only TRAIN-period attribution + abstract regime diagnosis.
    for blind_token in ("blind", "2022", "2023", "2024", "2025"):
        assert blind_token not in prompt.lower(), f"prompt leaks blind token: {blind_token}"
    assert "regime" in prompt.lower()
    assert "RISING_FAST" in prompt or "rising_fast" in prompt.lower()
