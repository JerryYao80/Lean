#!/usr/bin/env python3
"""TDD test for Scripts/gold2_data_gates.py (plan Task 11).
Covers both pass-path (real data) and failure-path (missing file → FAIL + exit 1)."""
import os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Scripts"))

import gold2_data_gates as gates  # FAILS until impl exists


def test_gate0_tushare_coverage_returns_true():
    ok, msg = gates.gate0_tushare_coverage()
    assert ok is True, f"expected pass, got FAIL: {msg}"


def test_gate0_fred_coverage_returns_true():
    ok, msg = gates.gate0_fred_coverage()
    assert ok is True, f"expected pass, got FAIL: {msg}"


def test_gate0_alignment_returns_true():
    """4-way join (518880/AU/VIX/DFII10). Verifies DFII10 is included — a 3-way
    only gate would pass even when DFII10 is misaligned (the original bug)."""
    ok, msg = gates.gate0_alignment()
    assert ok is True, f"expected pass, got FAIL: {msg}"


def test_cli_passes_exit_0():
    """python3 Scripts/gold2_data_gates.py must exit 0 and print ALL GATES PASS."""
    script = os.path.join(HERE, "..", "Scripts", "gold2_data_gates.py")
    r = subprocess.run([sys.executable, script], capture_output=True, text=True)
    assert r.returncode == 0, f"exit={r.returncode} stderr={r.stderr}"
    assert "ALL GATES PASS" in r.stdout, f"missing banner: {r.stdout}"


def test_cli_missing_data_exits_1():
    """Failure-path: pointing GOLD2_TUSHARE_DIR at a nonexistent dir must exit 1
    with a clean GATE FAIL banner (no traceback), proving the gate actually gates."""
    script = os.path.join(HERE, "..", "Scripts", "gold2_data_gates.py")
    env = dict(os.environ)
    env["GOLD2_TUSHARE_DIR"] = "/nonexistent/tushare/does-not-exist"
    env["GOLD2_DATA_DIR"] = "/nonexistent/data/does-not-exist"
    r = subprocess.run([sys.executable, script], capture_output=True, text=True, env=env)
    assert r.returncode == 1, f"expected exit 1, got {r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
    assert "GATE FAIL" in r.stderr, f"missing GATE FAIL banner in stderr: {r.stderr}"
    assert "Traceback" not in r.stderr, f"unexpected traceback (should be clean): {r.stderr}"


def test_alignment_uses_dfii10():
    """Regression guard for the DFII10 omission bug: alignment gate must read
    dfii10.csv. We monkeypatch pd.read_csv to detect dfii10 access."""
    import pandas as pd
    orig_read_csv = pd.read_csv
    seen = {}
    def spy_read_csv(path, *a, **kw):
        if "dfii10" in str(path).lower():
            seen["dfii10"] = True
        return orig_read_csv(path, *a, **kw)
    pd.read_csv = spy_read_csv
    try:
        gates.gate0_alignment()
    finally:
        pd.read_csv = orig_read_csv
    assert seen.get("dfii10"), "gate0_alignment did not read dfii10.csv (regression: 3-way join bug)"


if __name__ == "__main__":
    # Lightweight runner so we can run without pytest if needed.
    failed = False
    for name in [n for n in dir() if n.startswith("test_")]:
        try:
            globals()[name]()
            print(f"PASS {name}")
        except Exception as e:
            failed = True
            print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)
