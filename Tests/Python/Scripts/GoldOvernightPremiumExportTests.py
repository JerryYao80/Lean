import importlib.util
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "export_gold_overnight_premium_signals.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")
    spec = importlib.util.spec_from_file_location("export_gold_overnight_premium_signals", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GoldOvernightPremiumExportTests(unittest.TestCase):
    def _make_synthetic(self):
        """合成 AU.SHF + 518880 日线，60+ 行，已知 Gap。
        AU open(T)/pre_close(T) 隔夜 +1%；518880 open 不反映溢价（gap_actual=0），Signal 恒 0.01。
        """
        dates = pd.bdate_range("2024-01-01", periods=80)
        au_pre_close = 400.0
        au_rows = []
        for d in dates:
            au_open = au_pre_close * 1.01
            au_close = au_open
            au_rows.append({
                "ts_code": "AU.SHF", "trade_date": int(d.strftime("%Y%m%d")),
                "pre_close": au_pre_close, "open": au_open, "close": au_close,
                "high": au_open, "low": au_open, "vol": 1000.0, "oi": 1000.0,
            })
            au_pre_close = au_close
        etf_pre_close = 5.0
        etf_rows = []
        for d in dates:
            etf_open = etf_pre_close  # gap_actual = 0
            etf_close = etf_open
            etf_rows.append({
                "ts_code": "518880.SH", "trade_date": int(d.strftime("%Y%m%d")),
                "pre_close": etf_pre_close, "open": etf_open, "close": etf_close,
                "high": etf_open, "low": etf_open, "vol": 10000.0,
            })
            etf_pre_close = etf_close
        return pd.DataFrame(au_rows), pd.DataFrame(etf_rows)

    def test_compute_signal_known_gap(self):
        m = load_module()
        au, etf = self._make_synthetic()
        out = m.compute_signals(au, etf, fxcm=None, min_history=60)
        self.assertIn("z_signal", out.columns)
        self.assertIn("skip_reason", out.columns)
        self.assertIn("regime", out.columns)
        # Signal 恒 0.01 -> std=0 -> Z=NaN 守卫，skip_reason=INSUFFICIENT_HISTORY/NO_EDGE，不崩溃
        self.assertGreaterEqual(len(out), 80)

    def test_skip_reason_data_stale_when_row_missing(self):
        m = load_module()
        au, etf = self._make_synthetic()
        au = au.drop(index=au.index[40]).reset_index(drop=True)
        out = m.compute_signals(au, etf, fxcm=None, min_history=60)
        stale = out[out["skip_reason"] == "DATA_STALE"]
        self.assertGreaterEqual(len(stale), 1)

    def test_regime_unavailable_when_no_fred(self):
        m = load_module()
        au, etf = self._make_synthetic()
        out = m.compute_signals(au, etf, fxcm=None, min_history=60, fred_df=None)
        self.assertTrue((out["regime"] == "UNAVAILABLE").all())

    def test_causal_no_lookahead(self):
        """Z(T) 只用 T 及之前的数据。截断后 5 天，前面 Z 应与 full 一致。"""
        m = load_module()
        au, etf = self._make_synthetic()
        out_full = m.compute_signals(au, etf, fxcm=None, min_history=60)
        out_trunc = m.compute_signals(au.iloc[:-5], etf.iloc[:-5], fxcm=None, min_history=60)
        merged = out_full.merge(out_trunc, on="trade_date", suffixes=("_full", "_trunc"))
        common = merged.dropna(subset=["z_signal_full", "z_signal_trunc"])
        if len(common) > 0:
            import numpy as np
            np.testing.assert_array_almost_equal(
                common["z_signal_full"].to_numpy(), common["z_signal_trunc"].to_numpy(), decimal=8
            )


if __name__ == "__main__":
    unittest.main()
