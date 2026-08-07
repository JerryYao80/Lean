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


def test_historical_provider_matches_reference_formula(tmp_path):
    """HistoricalCovarianceProvider must match the P2-B reference formula
    (NaN col-mean impute -> LedoitWolf -> x252 -> +1e-8 ridge), computed
    inline here so a regression in the provider is caught. The inline
    reference is independent of the provider code (a regression in the
    provider would be caught by the assert_allclose)."""
    from export_mvo_weights import HistoricalCovarianceProvider
    from sklearn.covariance import LedoitWolf
    rng = np.random.default_rng(3)
    arr = rng.normal(0, 0.01, (120, 10))
    # inject some NaN to exercise the impute path
    arr[5, 2] = np.nan
    arr[10, 7] = np.nan
    returns = pd.DataFrame(arr, columns=[f"s{i}" for i in range(10)])
    # Reference: P2-B formula inline (independent of the provider implementation)
    ref = arr.copy()
    col_mean = np.nanmean(ref, axis=0)
    col_mean = np.where(np.isnan(col_mean), 0.0, col_mean)
    inds = np.where(np.isnan(ref))
    ref[inds] = np.take(col_mean, inds[1])
    lw = LedoitWolf().fit(ref)
    expected = lw.covariance_ * 252.0 + np.eye(10) * 1e-8
    # Provider
    provider = HistoricalCovarianceProvider()
    sigma, meta = provider.estimate(returns.columns.tolist(), "2024-01-31", returns=returns)
    np.testing.assert_allclose(sigma, expected, atol=1e-12)
    assert meta["cov_source"] == "historical"


def test_barra_provider_constructs_structured_covariance(tmp_path):
    """BarraCovarianceProvider: Sigma = B_t Sigma_f B_t' + diag(Delta) + conditioning.
    After eigenvalue conditioning (clip negatives + relative ridge), Sigma must be
    symmetric positive definite and close to the raw structured form."""
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
    raw = B @ sigma_f @ B.T + np.diag([delta[s] for s in symbols])
    # Conditioning adds ridge on small eigenvalues; sigma should be close to raw
    # but with all eigenvalues >= ridge_threshold (max_eigval * 1e-6).
    np.testing.assert_allclose(sigma, sigma.T, atol=1e-10)  # symmetric
    eigs = np.linalg.eigvalsh(sigma)
    assert (eigs > 0).all(), f"non-PD eigenvalues: {eigs}"
    # Close to raw (within 5% relative for well-conditioned directions)
    np.testing.assert_allclose(sigma, raw, rtol=0.05)
    assert meta["cov_source"] == "barra"
    assert meta["barra_risk_used"] == "barra_risk_2024-01-31.json"


def test_barra_provider_missing_exposure_floored_not_zero(tmp_path):
    """C1: missing-exposure symbol gets median-delta floor (conservative high
    variance), NOT zero — zero would make MVO concentrate max_weight into it."""
    from export_mvo_weights import BarraCovarianceProvider
    sigma_f = np.array([[0.04, 0.0], [0.0, 0.04]])
    # s0 has exposure+delta; s1 has NEITHER (missing)
    risk_file = tmp_path / "barra_risk_2024-01-31.json"
    risk_file.write_text(json.dumps({
        "as_of": "2024-01-31", "factors": ["f0", "f1"],
        "sigma_f": sigma_f.tolist(),
        "delta": {"s0": 0.10},  # s1 missing
        "exposures": {"s0": [1.0, 0.0]},  # s1 missing
        "est_window": 504, "decay_halflife": 252, "n_symbols": 1, "n_obs": 504, "fallback": None,
    }))
    provider = BarraCovarianceProvider(barra_risk_dir=str(tmp_path))
    sigma, meta = provider.estimate(["s0", "s1"], "2024-01-31")
    # s1 missing both -> delta floored to median of available = 0.10, exposure 0
    # sigma[1,1] = 0 (B row 0) + 0.10 (floored delta) + 1e-8 ridge
    assert sigma[1, 1] >= 0.09  # floored, NOT ~1e-8
    assert sigma[0, 1] == 0.0  # s1 has no exposure -> no cross-term
    assert meta["cov_source"] == "barra"
    assert meta["barra_risk_used"] == "barra_risk_2024-01-31.json"


def test_barra_provider_corrupt_json_raises(tmp_path):
    """I2: corrupt barra_risk JSON raises (was masked as 'no data' -> stale weights)."""
    from export_mvo_weights import BarraCovarianceProvider
    risk_file = tmp_path / "barra_risk_2024-01-31.json"
    risk_file.write_text("{not valid json")
    provider = BarraCovarianceProvider(barra_risk_dir=str(tmp_path))
    with pytest.raises(ValueError, match="Corrupt barra_risk file"):
        provider.estimate(["s0"], "2024-01-31")


def test_barra_provider_majority_missing_raises(tmp_path):
    """C1: >50% symbols missing exposure -> ValueError (systemic ticker mismatch)."""
    from export_mvo_weights import BarraCovarianceProvider
    sigma_f = np.array([[0.04, 0.0], [0.0, 0.04]])
    risk_file = tmp_path / "barra_risk_2024-01-31.json"
    risk_file.write_text(json.dumps({
        "as_of": "2024-01-31", "factors": ["f0", "f1"],
        "sigma_f": sigma_f.tolist(),
        "delta": {"s0": 0.10},  # only s0
        "exposures": {"s0": [1.0, 0.0]},  # only s0; 3/4 missing -> >50%
        "est_window": 504, "decay_halflife": 252, "n_symbols": 1, "n_obs": 504, "fallback": None,
    }))
    provider = BarraCovarianceProvider(barra_risk_dir=str(tmp_path))
    with pytest.raises(ValueError, match="ticker-format mismatch"):
        provider.estimate(["s0", "s1", "s2", "s3"], "2024-01-31")


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
