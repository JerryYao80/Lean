"""Tests for hypothesize.run (mock LLM). Spec §3.1-3.3."""
import sys
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
from hypothesize import run, build_prompt  # noqa: E402

_REVIEW_DOC = {
    "layer_attribution": {
        "extreme_risk": {"pnl_abs": "-1000", "pnl_pct_of_total": -0.32, "n_trades": 50, "n_wins": 10},
    },
    "per_trade_narrative": [
        {"symbol": "518880", "layer_contributions": {"extreme_risk": "-200"}, "entry_time": "2021-01-01"},
    ],
    "drawdown_attribution": [],
}
_GEN_HISTORY = [
    {"generation": 1, "layer_gaps": {"extreme_risk": {"gap": 0.32}}, "shaping_overrides": {"extreme_risk_contrib_penalty": 0.5}},
    {"generation": 2, "layer_gaps": {"extreme_risk": {"gap": 0.33}}, "shaping_overrides": {"extreme_risk_contrib_penalty": 1.5}},
    {"generation": 3, "layer_gaps": {"extreme_risk": {"gap": 0.34}}, "shaping_overrides": {"extreme_risk_contrib_penalty": 2.13}},
]


def test_build_prompt_contains_strategy_and_layer():
    prompt = build_prompt("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                          layer_semantic="extreme risk cap", threshold=0.15)
    assert "Gold2" in prompt
    assert "extreme_risk" in prompt
    assert "0.32" in prompt
    assert "2.13" in prompt


def test_run_writes_markdown_with_mock_llm(tmp_path):
    mock_md = "# 受 Gold2 extreme_risk 层失效启发的策略假设\n\n## 根因诊断\ncap 是二元阈值..." + "x" * 100
    with patch("hypothesize._call_llm", return_value=mock_md):
        path = run("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                   {"review": {"layer_names": ["trend", "vol_target", "extreme_risk", "realrate_cap"]}},
                   {"config_key": "code-generation-llm", "model": "glm-5.1", "temperature": 0.7},
                   hypothesis_dir=str(tmp_path))
    assert Path(path).exists()
    content = Path(path).read_text()
    assert "根因诊断" in content
    assert len(content) > 100


def test_run_raises_on_empty_llm_output(tmp_path):
    with patch("hypothesize._call_llm", return_value="short"):
        import pytest
        with pytest.raises(ValueError, match="100"):
            run("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                {"review": {"layer_names": ["extreme_risk"]}}, {}, hypothesis_dir=str(tmp_path))


def test_run_raises_on_llm_exception(tmp_path):
    with patch("hypothesize._call_llm", side_effect=RuntimeError("network error")):
        import pytest
        with pytest.raises(RuntimeError, match="network"):
            run("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                {"review": {"layer_names": ["extreme_risk"]}}, {}, hypothesis_dir=str(tmp_path))


# ── Phase 4: FactorCatalog injection into the prompt ─────────────────────────
_AVAILABLE_FACTORS = [
    {"id": "crowding", "name": "Trading Crowding Score", "category": "Sentiment",
     "selection_hint": "拥挤度, 高=过热, 通常反向"},
    {"id": "hv_20d", "name": "20-Day Realized Volatility", "category": "Volatility",
     "selection_hint": "20日已实现波动率, 高=高波动"},
]


def test_build_prompt_includes_available_factors_when_provided():
    prompt = build_prompt("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                          layer_semantic="extreme risk cap", threshold=0.15,
                          available_factors=_AVAILABLE_FACTORS)
    assert "## 可选用因子" in prompt
    assert "crowding" in prompt
    assert "拥挤度" in prompt
    assert "hv_20d" in prompt


def test_build_prompt_omits_factor_section_when_empty():
    # Empty/None available_factors must NOT add the section (graceful degrade;
    # keeps existing prompt assertions valid when no catalog is present).
    prompt_empty = build_prompt("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                                layer_semantic="extreme risk cap", threshold=0.15,
                                available_factors=[])
    assert "## 可选用因子" not in prompt_empty
    prompt_none = build_prompt("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                               layer_semantic="extreme risk cap", threshold=0.15)
    assert "## 可选用因子" not in prompt_none


def test_run_loads_catalog_from_file(tmp_path, monkeypatch):
    import yaml as _yaml
    catalog = {"version": "2026-07-25T00:00:00+00:00", "universe": ["CSI300"],
               "factors": _AVAILABLE_FACTORS}
    cat_path = tmp_path / "factor-catalog.yaml"
    cat_path.write_text(_yaml.safe_dump(catalog, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr("hypothesize.CATALOG_PATH", cat_path)

    captured = {}

    def _capture(system_prompt, user_prompt, llm_cfg):
        captured["user_prompt"] = user_prompt
        return "# 假设\n\n" + "x" * 100

    with patch("hypothesize._call_llm", side_effect=_capture):
        run("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
            {"review": {"layer_names": ["extreme_risk"]}}, {}, hypothesis_dir=str(tmp_path))
    assert "## 可选用因子" in captured["user_prompt"]
    assert "crowding" in captured["user_prompt"]
