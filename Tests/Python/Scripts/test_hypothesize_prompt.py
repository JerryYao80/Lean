"""Verify hypothesize LLM prompt includes alpha101 intent/direction/family
(and excludes scenarios list — too verbose for prompt)."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "Scripts" / "inspiration"))
import hypothesize as h  # noqa: E402


def _fake_factors():
    return [{
        "id": "alpha042",
        "name": "WorldQuant Alpha#42",
        "category": "Alpha101",
        "selection_hint": "VWAP-收盘均值回归",
        "intent": "VWAP-收盘均值回归。衡量当日 VWAP 相对收盘价的偏离。",
        "direction": "反向",
        "family": "VWAP反转",
        "scenarios": ["短期反转选股", "日内择时"],
    }]


def test_prompt_includes_intent_direction_family():
    prompt = h.build_prompt("S", "L", {}, [], "L", 0.15,
                            available_factors=_fake_factors())
    assert "VWAP-收盘均值回归。衡量当日 VWAP" in prompt, "intent missing"
    assert "反向" in prompt, "direction missing"
    assert "VWAP反转" in prompt, "family missing"


def test_prompt_excludes_scenarios():
    prompt = h.build_prompt("S", "L", {}, [], "L", 0.15,
                            available_factors=_fake_factors())
    # scenarios 列表内容不应进提示
    assert "短期反转选股" not in prompt, "scenarios leaked into prompt"
    assert "日内择时" not in prompt, "scenarios leaked into prompt"


def test_prompt_omits_factors_section_when_empty():
    prompt = h.build_prompt("S", "L", {}, [], "L", 0.15, available_factors=[])
    assert "可选用因子" not in prompt
