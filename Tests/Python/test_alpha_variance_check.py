import pathlib, pytest
from alpha_variance_check import check_alpha_variance, AlphaVarianceReport

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

def test_constant_alpha_detected_as_low_variance():
    report = check_alpha_variance(FIXTURES / "trace_constant_alpha.jsonl")
    assert report.n_samples == 3
    assert report.alpha_variance < 0.01
    assert report.needs_diversification is True

def test_diverse_alpha_passes_variance_threshold():
    report = check_alpha_variance(FIXTURES / "trace_diverse_alpha.jsonl")
    assert report.alpha_variance > 0.01
    assert report.needs_diversification is False

def test_missing_alpha_field_treated_as_constant():
    import json, tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write('{"ts":"2024-01-01","tpv":1000000,"alpha":1.0}\n')
        f.write('{"ts":"2024-01-02","tpv":1001000,"alpha":1.0}\n')
        path = f.name
    try:
        report = check_alpha_variance(path)
        assert report.needs_diversification is True
    finally:
        os.unlink(path)
