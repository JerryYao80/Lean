from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from export_barra_risk import BarraRiskExporter, FACTORS


def _make_factor_returns(n_dates=10, n_symbols=5, n_factors=3, seed=42):
    """Synthetic B (dates×symbols×factors), r (dates×symbols) with known f."""
    rng = np.random.default_rng(seed)
    B = rng.normal(0, 1, (n_dates, n_symbols, n_factors))
    true_f = rng.normal(0, 1, (n_dates, n_factors))
    eps = rng.normal(0, 0.01, (n_dates, n_symbols))
    r = np.einsum("dsf,df->ds", B, true_f) + eps
    return B, r, true_f


def test_cross_sectional_regression_recovers_factor_returns():
    """截面 OLS r_t = B_t·f_t + ε_t should recover true f (low noise)."""
    B, r, true_f = _make_factor_returns(n_dates=20, n_symbols=50, n_factors=3, seed=7)
    exporter = BarraRiskExporter.__new__(BarraRiskExporter)  # bypass __init__ (no dirs)
    f_hat, residuals = exporter._cross_sectional_regression(B, r)
    assert f_hat.shape == (20, 3)
    assert residuals.shape == (20, 50)
    np.testing.assert_allclose(f_hat, true_f, atol=0.05)


def test_factor_covariance_symmetric_positive_definite():
    rng = np.random.default_rng(11)
    f = rng.normal(0, 0.01, (300, 15))
    exporter = BarraRiskExporter.__new__(BarraRiskExporter)
    exporter.est_window = 504
    exporter.decay_halflife = 252
    sigma_f = exporter._estimate_factor_cov(f)
    assert sigma_f.shape == (15, 15)
    np.testing.assert_allclose(sigma_f, sigma_f.T, atol=1e-10)
    eigs = np.linalg.eigvalsh(sigma_f)
    assert eigs.min() > 0


def test_specific_var_exponential_decay_and_nonneg():
    """exp-decay variance with halflife=252 on constant series ~0; non-negative."""
    rng = np.random.default_rng(5)
    residuals = rng.normal(0, 0.02, (504, 4))
    exporter = BarraRiskExporter.__new__(BarraRiskExporter)
    exporter.est_window = 504
    exporter.decay_halflife = 252
    delta = exporter._estimate_specific_var(residuals, ["s0", "s1", "s2", "s3"])
    assert len(delta) == 4
    for v in delta.values():
        assert v >= 0  # non-negative
    # Annualized var of 0.02 daily vol -> ~0.02^2*252 = 0.1008
    np.testing.assert_allclose(delta["s0"], 0.02**2 * 252, rtol=0.3)


def test_specific_var_handles_nan_and_insufficient_obs():
    """_estimate_specific_var: columns with <2 valid obs -> NaN, others estimated."""
    residuals = np.array([
        [0.01, np.nan, 0.02],
        [0.02, np.nan, -0.01],
        [0.0, 0.03, 0.0],
        [0.01, 0.04, -0.02],
    ], dtype=float)
    exporter = BarraRiskExporter.__new__(BarraRiskExporter)
    exporter.est_window = 504
    exporter.decay_halflife = 252
    delta = exporter._estimate_specific_var(residuals, ["a", "b", "c"])
    # symbol "b" has only 2 valid obs (rows 2,3) — >=2 so estimated, not NaN
    assert "b" in delta
    assert delta["b"] >= 0
    # all entries finite non-negative or NaN-flagged via absence (delta filters NaN at payload)
    for v in delta.values():
        assert (v >= 0) or np.isnan(v)




def test_load_factor_and_returns_no_lookahead(tmp_path):
    """Real no-lookahead: _load_factor_and_returns must exclude as_of day.

    Plants a minimal factor CSV + daily parquet + adj_factor parquet spanning
    20240130 and 20240131 (as_of). Calls the REAL _load_factor_and_returns
    (no monkeypatch of the method itself) and asserts the as-of day never
    appears in the returned B_panel keys or r_panel index.
    """
    factor_dir = tmp_path / "sse" / "daily"
    factor_dir.mkdir(parents=True)
    daily_dir = tmp_path / "daily"
    adj_dir = tmp_path / "adj_factor"

    factor_rows = []
    for d in ("20240130", "20240131"):
        row = {"trade_date": d}
        row.update({f: 0.1 for f in FACTORS})
        factor_rows.append(row)
    pd.DataFrame(factor_rows).to_csv(factor_dir / "600519.csv", index=False)

    daily_ts_dir = daily_dir / "ts_code=600519.SH"
    daily_ts_dir.mkdir(parents=True)
    daily_df = pd.DataFrame({
        "trade_date": ["20240130", "20240131"],
        "close": [100.0, 101.0],
    })
    daily_df.to_parquet(daily_ts_dir / "part.parquet")

    adj_ts_dir = adj_dir / "ts_code=600519.SH"
    adj_ts_dir.mkdir(parents=True)
    adj_df = pd.DataFrame({
        "trade_date": ["20240130", "20240131"],
        "adj_factor": [1.0, 1.0],
    })
    adj_df.to_parquet(adj_ts_dir / "part.parquet")

    exporter = BarraRiskExporter(
        factor_data_dir=str(tmp_path),
        daily_data_dir=str(daily_dir),
        adj_factor_dir=str(adj_dir),
        est_window=504,
    )
    B_panel, r_panel = exporter._load_factor_and_returns("2024-01-31", 504)

    # as-of day (20240131) must be EXCLUDED from both panels.
    assert "20240131" not in B_panel
    r_dates = set(r_panel.index.strftime("%Y%m%d"))
    assert "20240131" not in r_dates
    # control: 20240130 should be present in B_panel (factor row survived).
    assert "20240130" in B_panel


def test_json_schema_complete(tmp_path):
    exporter = BarraRiskExporter(factor_data_dir=str(tmp_path), daily_data_dir=str(tmp_path),
                                 adj_factor_dir=str(tmp_path))
    def fake_compute(as_of):
        return {
            "as_of": as_of, "factors": FACTORS,
            "sigma_f": [[0.01]*15 for _ in range(15)],
            "delta": {"600519.SH": 0.12},
            "exposures": {"600519.SH": [0.1]*15},
            "est_window": 504, "decay_halflife": 252,
            "n_symbols": 1, "n_obs": 504, "fallback": None,
        }
    exporter._compute_risk_for_date = fake_compute
    written = exporter.export_for_dates(["2024-01-31"], str(tmp_path), force=True)
    assert len(written) == 1
    data = json.loads(written[0].read_text())
    for key in ["as_of", "factors", "sigma_f", "delta", "exposures",
                "est_window", "decay_halflife", "n_symbols", "n_obs", "fallback"]:
        assert key in data, f"missing {key}"
    assert len(data["factors"]) == 15
    assert len(data["sigma_f"]) == 15
    assert len(data["sigma_f"][0]) == 15


def test_insufficient_obs_returns_none(tmp_path):
    exporter = BarraRiskExporter(factor_data_dir=str(tmp_path), daily_data_dir=str(tmp_path),
                                 adj_factor_dir=str(tmp_path), est_window=504)
    exporter._load_factor_and_returns = lambda as_of, w: (None, None)
    assert exporter._compute_risk_for_date("2024-01-31") is None


def test_csv_to_tscode_mapping():
    assert BarraRiskExporter._csv_to_tscode(Path("/x/sse/daily/600519.csv")) == "600519.SH"
    assert BarraRiskExporter._csv_to_tscode(Path("/x/szse/daily/000858.csv")) == "000858.SZ"
    assert BarraRiskExporter._csv_to_tscode(Path("/x/other/daily/600519.csv")) is None


def test_diagonal_fallback_when_few_symbols(tmp_path):
    """spec §4.1: <30 symbols -> diagonal Sigma_f with fallback='diagonal'."""
    exporter = BarraRiskExporter(factor_data_dir=str(tmp_path), daily_data_dir=str(tmp_path),
                                 adj_factor_dir=str(tmp_path), est_window=504, decay_halflife=252)
    rng = np.random.default_rng(1)
    f_hat = rng.normal(0, 0.01, (100, 15))
    residuals = rng.normal(0, 0.01, (100, 5))
    all_symbols = [f"s{i}.SH" for i in range(5)]
    # B_t with only 5 symbols (<30) -> triggers diagonal fallback
    B_t = {f"s{i}.SH": rng.normal(0, 1, 15).tolist() for i in range(5)}

    exporter._load_factor_and_returns = lambda as_of, w: ({}, pd.DataFrame())
    exporter._cross_sectional_regression_panel = lambda Bp, rp: (f_hat, residuals, all_symbols)
    exporter._load_factor_exposure_asof = lambda as_of: B_t
    payload = exporter._compute_risk_for_date("2024-01-31")
    assert payload is not None
    assert payload["fallback"] == "diagonal"
    # diagonal Sigma_f: off-diagonal ~0
    sf = np.array(payload["sigma_f"])
    off_diag = sf - np.diag(np.diag(sf))
    assert np.abs(off_diag).max() < 1e-12
