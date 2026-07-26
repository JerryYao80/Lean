# Tests/Python/FactorZoo/test_alpha101_lookback.py
"""Static lookback walker: each formula's required window >= sum of nested literals."""
import sys, inspect
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
from alpha101 import formulas as F  # noqa: E402
from alpha101.lookback import required_lookback, MAX_LOOKBACK  # noqa: E402


def test_alpha_032_lookback():
    assert required_lookback(F.alpha_032) >= 230

def test_alpha_019_lookback():
    assert required_lookback(F.alpha_019) >= 250

def test_max_lookback_covers_all():
    for i in range(1, 102):
        lb = required_lookback(getattr(F, f"alpha_{i:03d}"))
        assert lb <= MAX_LOOKBACK, f"alpha_{i:03d} needs {lb} > MAX_LOOKBACK {MAX_LOOKBACK}"

def test_default_panel_lookback_ge_max():
    from alpha101.panel_loader import load_panel
    assert inspect.signature(load_panel).parameters["lookback_days"].default >= MAX_LOOKBACK
