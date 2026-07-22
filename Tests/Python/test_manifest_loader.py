import pathlib
from manifest_loader import load_manifest, StrategyManifest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

def test_valid_manifest_parses():
    m = load_manifest(FIXTURES / "manifest_sample.yaml")
    assert isinstance(m, StrategyManifest)
    assert m.strategy_name == "TestStrategy"
    assert len(m.parameter_space) == 2
    assert m.parameter_space[0].name == "iv-rv-z-score-threshold"
    assert m.parameter_space[0].range == (1.5, 3.5)
    assert m.parameter_space[1].default == 0.02
    assert m.state_schema.dim_hint == 5
    assert m.reward_config.primary == "dsr"
    assert m.universe.symbols == ["510050"]

def test_missing_strategy_name_raises():
    import pytest
    bad = FIXTURES / "manifest_bad.yaml"
    bad.write_text("lean_config: x.json\n")
    try:
        with pytest.raises(ValueError, match="strategy_name"):
            load_manifest(bad)
    finally:
        bad.unlink()

def test_rl_state_completeness_parsed():
    m = load_manifest(FIXTURES / "manifest_sample.yaml")
    assert m.rl_state_completeness == "full"
