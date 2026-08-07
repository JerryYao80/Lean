import pytest
from manifest_lint import lint_manifest
from manifest_loader import StrategyManifest

def _make_manifest(completeness: str) -> StrategyManifest:
    return StrategyManifest(
        strategy_name="t", lean_config="x.json", risk_model_target="",
        parameter_space=[], state_schema=type("S",(),{"fields":[],"dim_hint":0})(),
        reward_config=type("R",(),{"primary":"dsr","shaping":[]})(),
        universe=type("U",(),{"symbols":[],"timezone":"Asia/Shanghai"})(),
        rl_state_completeness=completeness)

def test_full_passes_completeness_gate():
    m = _make_manifest("full")
    r = lint_manifest(m, code_params=set(), state_fields=set())
    assert r.ok

def test_partial_blocked():
    m = _make_manifest("partial")
    r = lint_manifest(m, code_params=set(), state_fields=set())
    assert not r.ok
    assert any("partial" in e and "rl_state_completeness" in e for e in r.errors)

def test_unaudited_blocked():
    m = _make_manifest("unaudited")
    r = lint_manifest(m, code_params=set(), state_fields=set())
    assert not r.ok
    assert any("unaudited" in e and "rl_state_completeness" in e for e in r.errors)
