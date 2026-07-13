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
