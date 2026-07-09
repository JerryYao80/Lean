#!/usr/bin/env python3
"""TDD test for Scripts/gold2_data_gates.py (plan Task 11).
Run BEFORE the implementation exists -> ImportError FAIL.
Run AFTER -> all three gate functions exist and pass."""
import os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Scripts"))

import gold2_data_gates as gates  # FAILS until impl exists


def test_gate0_tushare_coverage_returns_true():
    assert gates.gate0_tushare_coverage() is True


def test_gate0_fred_coverage_returns_true():
    assert gates.gate0_fred_coverage() is True


def test_gate0_alignment_returns_true():
    assert gates.gate0_alignment() is True


def test_cli_passes_exit_0():
    """python3 Scripts/gold2_data_gates.py must exit 0 and print ALL GATES PASS."""
    script = os.path.join(HERE, "..", "Scripts", "gold2_data_gates.py")
    r = subprocess.run([sys.executable, script], capture_output=True, text=True)
    assert r.returncode == 0, f"exit={r.returncode} stderr={r.stderr}"
    assert "ALL GATES PASS" in r.stdout, f"missing banner: {r.stdout}"


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
