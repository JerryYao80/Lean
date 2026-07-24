import importlib.util
import unittest
from pathlib import Path
import pandas as pd
import numpy as np


def load_module():
    p = Path(__file__).resolve().parents[3] / "Scripts" / "gold_overnight_premium_gates.py"
    spec = importlib.util.spec_from_file_location("gold_overnight_premium_gates", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class GoldOvernightPremiumGateTests(unittest.TestCase):
    def _sig(self, n=80, stale=0, z_nan=0):
        dates = pd.bdate_range("2024-01-01", periods=n)
        z = np.full(n, 1.6)
        if z_nan:
            z[:z_nan] = np.nan
        df = pd.DataFrame({
            "trade_date": [int(d.strftime("%Y%m%d")) for d in dates],
            "z_signal": z, "regime": "UNAVAILABLE", "skip_reason": "NONE",
            "signal": 0.01, "gap_expected": 0.01, "gap_actual": 0.0,
            "freshness_flag": True, "cross_check_alert": False,
        })
        if stale:
            df.loc[df.index[40], "skip_reason"] = "DATA_STALE"
        return df

    def test_gate0_zeroed_field_detects_all_nan(self):
        m = load_module()
        sig = self._sig(z_nan=80)
        ok, msg = m.gate0_zeroed_field(sig, threshold_pct=0.05)
        self.assertFalse(ok)  # 100% NaN > 5%

    def test_gate0_zeroed_field_passes_clean(self):
        m = load_module()
        sig = self._sig(z_nan=0)
        ok, _ = m.gate0_zeroed_field(sig, threshold_pct=0.05)
        self.assertTrue(ok)

    def test_gate1_cost_sensitivity(self):
        m = load_module()
        ok, _ = m.gate1_cost_sensitivity(self._sig(), cost_bps=15)
        self.assertTrue(ok)  # gap 100bps > 15bps
        sig = self._sig(); sig["gap_expected"] = 0.001
        ok, _ = m.gate1_cost_sensitivity(sig, cost_bps=15)
        self.assertFalse(ok)  # 10bps < 15bps

    def test_gate2_ic_kill_switch_fails_when_no_predictive_power(self):
        m = load_module()
        sig = self._sig()
        sig["forward_return"] = np.random.RandomState(42).randn(len(sig))  # 纯噪声
        ok, _ = m.gate2_ic_kill_switch(sig, oos_split=0.7)
        self.assertFalse(ok)

    def test_gate2_ic_kill_switch_passes_with_predictive_signal(self):
        m = load_module()
        sig = self._sig()
        # signal needs variance for Pearson IC to be defined; constant signal → NaN IC → fail
        sig["signal"] = np.linspace(0.01, 0.05, len(sig))
        sig["forward_return"] = sig["signal"] * 5 + np.random.RandomState(42).randn(len(sig)) * 0.01
        ok, _ = m.gate2_ic_kill_switch(sig, oos_split=0.7)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
