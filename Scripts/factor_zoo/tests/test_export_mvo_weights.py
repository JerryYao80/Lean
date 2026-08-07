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
    # Inject fakes — _load_alpha_scores returns (scores, report_name) tuple.
    exporter._load_alpha_scores = lambda as_of: (_fake_alpha_scores(symbols),
                                                 "ic_report_test.json")
    exporter._load_historical_returns = lambda as_of, w: _fake_returns(symbols, w)

    payload = exporter._compute_weights_for_date("2024-01-31")
    assert payload is not None
    assert abs(payload["sum"] - 1.0) < 1e-6, f"sum={payload['sum']}"
    for sym in payload["symbols"]:
        assert sym["weight"] <= 0.15 + 1e-6, f"{sym} exceeds max_weight"
    assert payload["n_assets"] == 10
    assert payload["as_of"] == "2024-01-31"
    assert payload["ic_report_used"] == "ic_report_test.json"


def test_no_lookahead_end_date_before_rebalance(tmp_path):
    """as_of=2024-01-31 must use returns strictly before 2024-01-31."""
    captured = {}
    symbols = [f"60000{i}.SH" for i in range(5)]
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x", cov_window=120)
    exporter._load_alpha_scores = lambda as_of: (_fake_alpha_scores(symbols),
                                                 "ic_report_test.json")

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
    exporter._load_alpha_scores = lambda as_of: (_fake_alpha_scores(symbols),
                                                 "ic_report_test.json")
    exporter._load_historical_returns = lambda as_of, w: _fake_returns(symbols, w)
    sigma = exporter._estimate_covariance(_fake_returns(symbols))
    eigs = np.linalg.eigvalsh(sigma)
    assert (eigs > 0).all(), f"non-PD eigenvalues: {eigs.min()}"


def test_json_schema_complete(tmp_path):
    symbols = [f"60000{i}.SH" for i in range(5)]
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x", cov_window=120, max_weight=0.20)
    exporter._load_alpha_scores = lambda as_of: (_fake_alpha_scores(symbols),
                                                 "ic_report_test.json")
    exporter._load_historical_returns = lambda as_of, w: _fake_returns(symbols, w)
    payload = exporter._compute_weights_for_date("2024-01-31")
    for key in ("as_of", "symbols", "sum", "lambda", "cov_window", "n_assets"):
        assert key in payload, f"missing {key}"
    assert all("ts_code" in s and "weight" in s for s in payload["symbols"])


def test_fallback_equal_weight_on_nonconvergence(tmp_path):
    """Degenerate Sigma (all-zero) forces fallback / clip path.

    n=4, max_weight=0.50 (feasible: 4*0.5=2>=1). Zero returns -> zero
    covariance -> SLSQP may converge to a corner solution or fail; either
    way the clip-and-renormalize defense must keep weights within bound.
    """
    symbols = [f"60000{i}.SH" for i in range(4)]
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x", max_weight=0.50)
    exporter._load_alpha_scores = lambda as_of: (_fake_alpha_scores(symbols),
                                                 "ic_report_test.json")
    zero_ret = pd.DataFrame(0.0, index=pd.bdate_range(end="2024-01-30", periods=120),
                            columns=symbols)
    exporter._load_historical_returns = lambda as_of, w: zero_ret
    payload = exporter._compute_weights_for_date("2024-01-31")
    assert payload is not None
    assert abs(payload["sum"] - 1.0) < 1e-6
    # C3: every weight must respect max_weight (clip defense-in-depth).
    for sym in payload["symbols"]:
        assert sym["weight"] <= 0.50 + 1e-6, f"{sym} exceeds max_weight"
    # Fallback flag is set when SLSQP failed; on zero covariance SLSQP may
    # legitimately succeed (objective = -mu'w, picks max-weight corner).
    # Either way the payload must be valid — the assertion above covers it.
    assert payload["fallback"] in (None, "equal_weight", "infeasible")


def test_infeasible_n_maxweight_returns_none(tmp_path):
    """C3: n*max_weight < 1 is infeasible -> _compute_weights_for_date None."""
    symbols = [f"60000{i}.SH" for i in range(5)]
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x", max_weight=0.10)
    exporter._load_alpha_scores = lambda as_of: (_fake_alpha_scores(symbols),
                                                 "ic_report_test.json")
    exporter._load_historical_returns = lambda as_of, w: _fake_returns(symbols, w)
    payload = exporter._compute_weights_for_date("2024-01-31")
    assert payload is None


def test_missing_alpha_returns_none(tmp_path):
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x")
    # _load_alpha_scores returns (None, None) when no report/panel.
    exporter._load_alpha_scores = lambda as_of: (None, None)
    assert exporter._compute_weights_for_date("2024-01-31") is None


def test_ic_report_picks_latest_at_or_before(tmp_path):
    """IC report selection: latest <= as_of; NO earliest-report fallback
    (earliest fallback would be a lookahead-in-disguise)."""
    for d in ("2024-01-31", "2024-02-29"):
        (tmp_path / f"ic_report_{d}.json").write_text(
            json.dumps({"report_date": d, "factors": []}))
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x")
    report, name = exporter._load_latest_ic_report("2024-02-15")
    assert report["report_date"] == "2024-01-31"  # latest <= 2024-02-15
    assert name == "ic_report_2024-01-31.json"
    # M3: as_of BEFORE the earliest report -> None (no lookahead fallback).
    report_before, name_before = exporter._load_latest_ic_report("2024-01-01")
    assert report_before is None
    assert name_before is None


def test_min_obs_filter_drops_short_history(tmp_path):
    """C2: symbols with < 60 non-NaN return observations are dropped."""
    symbols = [f"60000{i}.SH" for i in range(5)]
    exporter = MVOWeightExporter(ic_report_dir=str(tmp_path), daily_data_dir="x",
                                 adj_factor_dir="x", cov_window=120, max_weight=0.50)
    exporter._load_alpha_scores = lambda as_of: (_fake_alpha_scores(symbols),
                                                 "ic_report_test.json")
    ret = _fake_returns(symbols, 120)
    # Starve one symbol of history (only 30 non-NaN rows -> < 60 threshold).
    ret.iloc[:90, 0] = np.nan
    exporter._load_historical_returns = lambda as_of, w: ret
    payload = exporter._compute_weights_for_date("2024-01-31")
    assert payload is not None
    ts_codes = [s["ts_code"] for s in payload["symbols"]]
    assert symbols[0] not in ts_codes, "short-history symbol should be dropped"
    assert len(payload["symbols"]) == 4


def test_historical_provider_backward_compatible(tmp_path):
    """HistoricalCovarianceProvider must produce identical sigma to P2-B _estimate_covariance."""
    from export_mvo_weights import HistoricalCovarianceProvider, MVOWeightExporter
    rng = np.random.default_rng(3)
    returns = pd.DataFrame(rng.normal(0, 0.01, (120, 10)),
                           columns=[f"s{i}" for i in range(10)])
    exporter = MVOWeightExporter.__new__(MVOWeightExporter)
    sigma_old = exporter._estimate_covariance(returns)
    provider = HistoricalCovarianceProvider()
    sigma_new, meta = provider.estimate(returns.columns.tolist(), "2024-01-31", returns=returns)
    np.testing.assert_allclose(sigma_new, sigma_old, atol=1e-12)
    assert meta["cov_source"] == "historical"


def test_barra_provider_constructs_structured_covariance(tmp_path):
    """BarraCovarianceProvider: Sigma = B_t Sigma_f B_t' + diag(Delta)."""
    from export_mvo_weights import BarraCovarianceProvider
    sigma_f = np.array([[0.04, 0.01], [0.01, 0.09]])
    delta = {"s0": 0.10, "s1": 0.20, "s2": 0.15}
    exposures = {"s0": [1.0, 0.5], "s1": [0.5, 1.0], "s2": [1.0, 1.0]}
    risk_file = tmp_path / "barra_risk_2024-01-31.json"
    risk_file.write_text(json.dumps({
        "as_of": "2024-01-31", "factors": ["f0", "f1"],
        "sigma_f": sigma_f.tolist(), "delta": delta, "exposures": exposures,
        "est_window": 504, "decay_halflife": 252, "n_symbols": 3, "n_obs": 504, "fallback": None,
    }))
    provider = BarraCovarianceProvider(barra_risk_dir=str(tmp_path))
    symbols = ["s0", "s1", "s2"]
    sigma, meta = provider.estimate(symbols, "2024-01-31")
    B = np.array([exposures[s] for s in symbols])
    expected = B @ sigma_f @ B.T + np.diag([delta[s] for s in symbols])
    np.testing.assert_allclose(sigma, expected, atol=1e-10)
    assert meta["cov_source"] == "barra"
    assert meta["barra_risk_used"] == "2024-01-31"


def test_cli_covariance_source_arg(monkeypatch, tmp_path):
    """main() --covariance-source barra wires BarraCovarianceProvider."""
    import export_mvo_weights as mod
    captured = {}
    def fake_export(self, dates, out_dir, force=False):
        captured["provider"] = type(self._cov_provider).__name__
        return []
    monkeypatch.setattr(mod.MVOWeightExporter, "export_for_dates", fake_export)
    monkeypatch.setattr(mod, "_month_end_dates", lambda s, e: ["2024-01-31"])
    argv = ["prog", "--start", "2024-01-31", "--end", "2024-01-31",
            "--covariance-source", "barra", "--barra-risk-dir", str(tmp_path),
            "--out-dir", str(tmp_path)]
    monkeypatch.setattr("sys.argv", argv)
    mod.main()
    assert captured["provider"] == "BarraCovarianceProvider"
