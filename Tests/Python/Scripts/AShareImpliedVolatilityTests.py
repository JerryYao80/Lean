"""Tests for ashare_implied_volatility.py."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

# Ensure Scripts is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Scripts.ashare_implied_volatility import (
    IV_MEASUREMENT,
    VIX_MEASUREMENT,
    IvResult,
    VixResult,
    bs_call_price,
    bs_d1,
    bs_d2,
    bs_put_price,
    bs_vega,
    compute_atm_iv,
    compute_daily_iv,
    compute_vix_for_term,
    find_forward_price,
    implied_vol_newton,
    iv_result_to_line,
    normalize_trade_date,
    to_float,
    trade_date_to_timestamp_ns,
    vix_result_to_line,
)


class TestBlackScholes(unittest.TestCase):
    """Test Black-Scholes pricing and Greeks."""

    def test_d1_d2_known_values(self):
        # S=100, K=100, T=1, r=0.05, sigma=0.2, q=0
        d1 = bs_d1(100, 100, 1, 0.05, 0.2)
        d2 = bs_d2(100, 100, 1, 0.05, 0.2)
        self.assertAlmostEqual(d1, 0.35, places=1)
        self.assertAlmostEqual(d2, 0.15, places=1)

    def test_call_price_atm(self):
        price = bs_call_price(100, 100, 1, 0.05, 0.2)
        self.assertAlmostEqual(price, 10.45, places=0)

    def test_put_price_atm(self):
        price = bs_put_price(100, 100, 1, 0.05, 0.2)
        self.assertAlmostEqual(price, 5.57, places=0)

    def test_put_call_parity(self):
        S, K, T, r, sigma = 100, 100, 1, 0.05, 0.2
        call = bs_call_price(S, K, T, r, sigma)
        put = bs_put_price(S, K, T, r, sigma)
        # C - P = S - K * e^(-rT)
        self.assertAlmostEqual(call - put, S - K * math.exp(-r * T), places=4)

    def test_call_price_zero_T(self):
        # At expiry, call = max(S-K, 0)
        self.assertEqual(bs_call_price(110, 100, 0, 0.05, 0.2), 10.0)
        self.assertEqual(bs_call_price(90, 100, 0, 0.05, 0.2), 0.0)

    def test_put_price_zero_T(self):
        self.assertEqual(bs_put_price(90, 100, 0, 0.05, 0.2), 10.0)
        self.assertEqual(bs_put_price(110, 100, 0, 0.05, 0.2), 0.0)

    def test_vega_positive(self):
        vega = bs_vega(100, 100, 1, 0.05, 0.2)
        self.assertGreater(vega, 0)

    def test_vega_zero_T(self):
        self.assertEqual(bs_vega(100, 100, 0, 0.05, 0.2), 0.0)


class TestImpliedVol(unittest.TestCase):
    """Test implied volatility computation."""

    def test_round_trip_call(self):
        """Compute call price at known IV, then recover IV."""
        S, K, T, r, true_iv = 2.7, 2.7, 30 / 365, 0.02, 0.25
        price = bs_call_price(S, K, T, r, true_iv)
        iv = implied_vol_newton(price, S, K, T, r, "C")
        self.assertIsNotNone(iv)
        self.assertAlmostEqual(iv, true_iv, places=3)

    def test_round_trip_put(self):
        S, K, T, r, true_iv = 2.7, 2.8, 30 / 365, 0.02, 0.30
        price = bs_put_price(S, K, T, r, true_iv)
        iv = implied_vol_newton(price, S, K, T, r, "P")
        self.assertIsNotNone(iv)
        self.assertAlmostEqual(iv, true_iv, places=3)

    def test_deep_itm_returns_none(self):
        # Price below intrinsic
        iv = implied_vol_newton(0.01, 100, 50, 1, 0.05, "C")
        self.assertIsNone(iv)

    def test_zero_T_returns_none(self):
        iv = implied_vol_newton(5.0, 100, 100, 0, 0.05, "C")
        self.assertIsNone(iv)

    def test_zero_price_returns_none(self):
        iv = implied_vol_newton(0, 100, 100, 1, 0.05, "C")
        self.assertIsNone(iv)


class TestNormalizeTradeDate(unittest.TestCase):
    def test_yyyymmdd(self):
        self.assertEqual(normalize_trade_date("20250115"), "20250115")

    def test_timestamp(self):
        import pandas as pd
        ts = pd.Timestamp("2025-01-15")
        self.assertEqual(normalize_trade_date(ts), "20250115")

    def test_none(self):
        self.assertEqual(normalize_trade_date(None), "")

    def test_float_string(self):
        self.assertEqual(normalize_trade_date("20250115.0"), "20250115")


class TestToFloat(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(to_float(3.14), 3.14)

    def test_none(self):
        self.assertIsNone(to_float(None))

    def test_nan(self):
        self.assertIsNone(to_float(float("nan")))

    def test_inf(self):
        self.assertIsNone(to_float(float("inf")))

    def test_string(self):
        self.assertEqual(to_float("2.5"), 2.5)


class TestTimestampConversion(unittest.TestCase):
    def test_known_date(self):
        ts = trade_date_to_timestamp_ns("20250115")
        # Should be Jan 15 2025 15:00 CST = 07:00 UTC
        self.assertIsInstance(ts, int)
        self.assertGreater(ts, 0)


class TestLineProtocol(unittest.TestCase):
    def test_iv_result_to_line(self):
        iv = IvResult(
            underlying="50ETF",
            trade_date="20250303",
            atm_iv=0.126,
            iv_call_25delta=0.127,
            iv_put_25delta=0.125,
            skew=-0.002,
            term_days_near=23,
            term_days_next=51,
            option_count=46,
        )
        line = iv_result_to_line(iv)
        self.assertIn("lean_ashare_iv", line)
        self.assertIn("underlying=50ETF", line)
        self.assertIn("trade_date=20250303", line)
        self.assertIn("atm_iv=", line)
        self.assertIn("skew=", line)

    def test_vix_result_to_line(self):
        vix = VixResult(
            underlying="50ETF",
            trade_date="20250303",
            vix=12.5,
            sigma_near=13.0,
            sigma_next=11.8,
            t_near=0.095,
            t_next=0.211,
        )
        line = vix_result_to_line(vix)
        self.assertIn("lean_ashare_vix", line)
        self.assertIn("vix=", line)
        self.assertIn("sigma_near=", line)


class TestComputeAtmIv(unittest.TestCase):
    def test_with_synthetic_options(self):
        """Create synthetic near-money options and verify IV is recovered."""
        import pandas as pd

        S = 2.7
        T = 30 / 365
        r = 0.02
        true_iv = 0.20

        strikes = [2.55, 2.60, 2.65, 2.70, 2.75, 2.80, 2.85]
        rows = []
        for K in strikes:
            call_price = bs_call_price(S, K, T, r, true_iv)
            put_price = bs_put_price(S, K, T, r, true_iv)
            rows.append({"exercise_price": K, "call_put": "C", "settle": call_price})
            rows.append({"exercise_price": K, "call_put": "P", "settle": put_price})

        df = pd.DataFrame(rows)
        iv = compute_atm_iv(df, S, T, r)
        self.assertIsNotNone(iv)
        self.assertAlmostEqual(iv, true_iv, places=2)


class TestFindForwardPrice(unittest.TestCase):
    def test_synthetic(self):
        import pandas as pd

        S, T, r, true_iv = 2.7, 30 / 365, 0.02, 0.20
        K = 2.7
        call_price = bs_call_price(S, K, T, r, true_iv)
        put_price = bs_put_price(S, K, T, r, true_iv)

        rows = [
            {"exercise_price": K, "call_put": "C", "settle": call_price},
            {"exercise_price": K, "call_put": "P", "settle": put_price},
        ]
        df = pd.DataFrame(rows)
        F = find_forward_price(df, r, T)
        self.assertIsNotNone(F)
        # F ≈ S * e^(rT) for ATM options
        expected_F = S * math.exp(r * T)
        self.assertAlmostEqual(F, expected_F, places=2)


class TestComputeVixForTerm(unittest.TestCase):
    def test_synthetic_vix(self):
        """With synthetic options at known IV, VIX should be close to that IV."""
        import pandas as pd

        S = 2.7
        T = 30 / 365
        r = 0.02
        true_iv = 0.25

        strikes = [i * 0.05 + 2.3 for i in range(17)]  # 2.30 to 3.10
        rows = []
        for K in strikes:
            call_price = bs_call_price(S, K, T, r, true_iv)
            put_price = bs_put_price(S, K, T, r, true_iv)
            rows.append({"exercise_price": K, "call_put": "C", "settle": call_price})
            rows.append({"exercise_price": K, "call_put": "P", "settle": put_price})

        df = pd.DataFrame(rows)
        F = find_forward_price(df, r, T)
        self.assertIsNotNone(F)

        variance = compute_vix_for_term(df, F, T, r)
        self.assertIsNotNone(variance)
        vix_pct = math.sqrt(variance) * 100
        # Should be close to 25%
        self.assertAlmostEqual(vix_pct, true_iv * 100, places=0)


if __name__ == "__main__":
    unittest.main()
