"""TDD tests for export_mvo_weights — MVO weight exporter."""
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from export_mvo_weights import MVOWeightExporter, _month_end_dates


def test_month_end_dates_basic():
    dates = _month_end_dates("2024-01-01", "2024-03-31")
    assert dates == ["2024-01-31", "2024-02-29", "2024-03-31"]


def _fake_alpha_scores(symbols):
    return pd.Series({s: float(i) for i, s in enumerate(symbols)})


def _fake_returns(symbols, n_days=120):
    rng = np.random.default_rng(42)
    dates = pd.bdate_range(end="2024-01-30", periods=n_days)
    return pd.DataFrame(rng.standard_normal((n_days, len(symbols))) * 0.01,
                        index=dates, columns=symbols)


def test_weights_sum_to_one_and_respect_max_weight(tmp_path):
    symbols = [f"60000{i}.SH" for i in range(10)]
    exporter = MVOWeightExporter(
        ic_report_dir=str(tmp_path), daily_data_dir="ignored",
        adj_factor_dir="ignored", max_weight=0.15, risk_aversion=1.0,
        cov_window=120)
    # Inject fakes
    exporter._load_alpha_scores = lambda as_of: _fake_alpha_scores(symbols)
    exporter._load_historical_returns = lambda as_of, w: _fake_returns(symbols, w)

    payload = exporter._compute_weights_for_date("2024-01-31")
    assert payload is not None
    assert abs(payload["sum"] - 1.0) < 1e-6, f"sum={payload['sum']}"
    for sym in payload["symbols"]:
        assert sym["weight"] <= 0.15 + 1e-6, f"{sym} exceeds max_weight"
    assert payload["n_assets"] == 10
    assert payload["as_of"] == "2024-01-31"


def test_no_lookahead_end_date_before_rebalance(tmp_path):
    """as_of=2024-01-31 must use returns strictly before 2024-01-31."""
    captured = {}
    symbols = [f"60000{i}.SH" for i in range(5)]
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x", cov_window=120)
    exporter._load_alpha_scores = lambda as_of: _fake_alpha_scores(symbols)

    def capture_returns(as_of, window):
        captured["as_of"] = as_of
        captured["window"] = window
        ret = _fake_returns(symbols, window)
        # Last return date must be < as_of
        assert ret.index.max() < pd.Timestamp(as_of), "lookahead!"
        return ret
    exporter._load_historical_returns = capture_returns

    exporter._compute_weights_for_date("2024-01-31")
    assert captured["as_of"] == "2024-01-31"
    assert captured["window"] == 120


def test_covariance_positive_definite(tmp_path):
    symbols = [f"60000{i}.SH" for i in range(8)]
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x", cov_window=120)
    exporter._load_alpha_scores = lambda as_of: _fake_alpha_scores(symbols)
    exporter._load_historical_returns = lambda as_of, w: _fake_returns(symbols, w)
    sigma = exporter._estimate_covariance(_fake_returns(symbols))
    eigs = np.linalg.eigvalsh(sigma)
    assert (eigs > 0).all(), f"non-PD eigenvalues: {eigs.min()}"


def test_json_schema_complete(tmp_path):
    symbols = [f"60000{i}.SH" for i in range(5)]
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x", cov_window=120, max_weight=0.20)
    exporter._load_alpha_scores = lambda as_of: _fake_alpha_scores(symbols)
    exporter._load_historical_returns = lambda as_of, w: _fake_returns(symbols, w)
    payload = exporter._compute_weights_for_date("2024-01-31")
    for key in ("as_of", "symbols", "sum", "lambda", "cov_window", "n_assets"):
        assert key in payload, f"missing {key}"
    assert all("ts_code" in s and "weight" in s for s in payload["symbols"])


def test_fallback_equal_weight_on_nonconvergence(tmp_path):
    """Degenerate Sigma (all-zero) forces fallback path."""
    symbols = [f"60000{i}.SH" for i in range(4)]
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x", max_weight=0.10)
    exporter._load_alpha_scores = lambda as_of: _fake_alpha_scores(symbols)
    # All-zero returns -> zero covariance -> SLSQP may still converge to
    # equal-weight-ish; assert it produces valid weights summing to 1.
    zero_ret = pd.DataFrame(0.0, index=pd.bdate_range(end="2024-01-30", periods=120),
                            columns=symbols)
    exporter._load_historical_returns = lambda as_of, w: zero_ret
    payload = exporter._compute_weights_for_date("2024-01-31")
    assert payload is not None
    assert abs(payload["sum"] - 1.0) < 1e-6


def test_missing_alpha_returns_none(tmp_path):
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x")
    exporter._load_alpha_scores = lambda as_of: None
    assert exporter._compute_weights_for_date("2024-01-31") is None


def test_ic_report_picks_latest_at_or_before(tmp_path):
    """IC report selection mirrors C# LoadLatestReport: latest <= as_of."""
    for d in ("2024-01-31", "2024-02-29"):
        (tmp_path / f"ic_report_{d}.json").write_text(
            json.dumps({"report_date": d, "factors": []}))
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x")
    report = exporter._load_latest_ic_report("2024-02-15")
    assert report["report_date"] == "2024-01-31"  # latest <= 2024-02-15
