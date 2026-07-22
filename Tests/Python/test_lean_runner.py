import pathlib, json
from lean_runner import parse_results

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

def test_parse_results_extracts_statistics():
    stats = parse_results(FIXTURES / "results_sample.json")
    assert stats["Sharpe Ratio"] == 1.23
    assert stats["Total Orders"] == 150

def test_missing_file_returns_empty():
    stats = parse_results(FIXTURES / "nonexistent.json")
    assert stats == {}
