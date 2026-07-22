import json, pathlib, pytest
from option_vol_arb_field_audit import audit_drawdown_consistency, audit_pnl_1d_sign, audit_days_held_boundary

def test_drawdown_consistent_within_tolerance():
    trace = [{"drawdown": 0.04, "tpv": 960000}]
    stats = {"Drawdown": "4.0%"}
    r = audit_drawdown_consistency(trace, stats, tolerance=0.01)
    assert r.consistent is True
    assert r.trace_max_drawdown == pytest.approx(0.04, abs=1e-6)
    assert r.stats_drawdown_fraction == pytest.approx(0.04, abs=1e-6)

def test_drawdown_inconsistent_flagged():
    trace = [{"drawdown": 0.04, "tpv": 960000}]
    stats = {"Drawdown": "10.0%"}
    r = audit_drawdown_consistency(trace, stats, tolerance=0.01)
    assert r.consistent is False

def test_missing_stats_drawdown_treated_as_inconclusive():
    trace = [{"drawdown": 0.04}]
    stats = {}
    r = audit_drawdown_consistency(trace, stats, tolerance=0.01)
    assert r.consistent is False
    assert "missing" in r.reason.lower()

def test_pnl_1d_sign_audit():
    trace = [{"pnl_1d": 0.01}, {"pnl_1d": -0.02}, {"pnl_1d": 0}]
    r = audit_pnl_1d_sign(trace)
    assert r["non_zero_count"] == 2
    assert 0 <= r["positive_ratio"] <= 1

def test_days_held_boundary_no_negative():
    trace = [{"positions": [{"days_held": 1}, {"days_held": 0}]}]
    r = audit_days_held_boundary(trace)
    assert r["ok"] is True
    trace2 = [{"positions": [{"days_held": -1}]}]
    r2 = audit_days_held_boundary(trace2)
    assert r2["ok"] is False
