import pathlib
from manifest_lint import lint_manifest, LintResult
from manifest_loader import load_manifest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

def test_param_mismatch_detected():
    m = load_manifest(FIXTURES / "manifest_sample.yaml")
    result = lint_manifest(m, code_params={"iv-rv-z-score-threshold"}, state_fields={"tpv", "drawdown"})
    assert not result.ok
    assert any("var-budget" in e for e in result.errors)

def test_state_schema_mismatch_detected():
    m = load_manifest(FIXTURES / "manifest_sample.yaml")
    result = lint_manifest(m, code_params={"iv-rv-z-score-threshold", "var-budget"}, state_fields={"tpv"})
    assert not result.ok
    assert any("drawdown" in e for e in result.errors)

def test_consistent_manifest_passes():
    m = load_manifest(FIXTURES / "manifest_sample.yaml")
    result = lint_manifest(m, code_params={"iv-rv-z-score-threshold", "var-budget"}, state_fields={"tpv", "drawdown"})
    assert result.ok, result.errors
