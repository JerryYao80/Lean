"""
export_barra_risk.py — Offline expanding-window Barra CNE5 factor risk generator.

For each month-end rebalance date t, computes:
  - Factor covariance Sigma_f (15x15) via cross-sectional OLS r_t = B_t·f_t + eps_t
    over the trailing est_window, then Ledoit-Wolf shrinkage + annualize (x252).
  - Specific variance Delta_s per symbol via exponential-decay variance of residuals
    (halflife=decay_halflife), annualized (x252).
  - As-of factor exposures B_t (N x 15) for MVO covariance construction.

Estimation window [as_of - est_window, as_of - 1] — STRICTLY before as_of (no lookahead).
as_of-day exposure B_t is used ONLY to construct Sigma = B_t Sigma_f B_t' + diag(Delta)
in the MVO provider; it does NOT enter Sigma_f estimation.

Output: result/barra-risk/barra_risk_YYYY-MM-DD.json
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

LOGGER = logging.getLogger("export_barra_risk")

DEFAULT_FACTOR_DIR = "/home/project/hope/Lean/Data/alternative/barra-cne5v2-factors_csi300_0608"
DEFAULT_DAILY_DIR = "/home/project/tushare-downloader/tushare_data_v2/daily"
DEFAULT_ADJ_DIR = "/home/project/tushare-downloader/tushare_data_v2/adj_factor"
DEFAULT_OUT_DIR = "/home/project/hope/Lean/result/barra-risk"

FACTORS = ["beta", "momentum", "size", "earnyld", "resvol", "growth",
           "btop", "leverage", "liquidity", "nlsize", "moneyflow",
           "quality", "northbound", "margin", "chipcost"]


def _month_end_dates(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    dates = []
    current = s
    while current <= e:
        next_first = current.replace(
            year=current.year + 1, month=1, day=1) if current.month == 12 \
            else current.replace(month=current.month + 1, day=1)
        month_end = next_first - timedelta(days=1)
        if month_end > e:
            break
        if month_end >= s:
            dates.append(month_end.strftime("%Y-%m-%d"))
        current = next_first
    return dates


class BarraRiskExporter:
    """Offline Barra factor risk exporter (expanding-window, no lookahead)."""

    def __init__(
        self,
        factor_data_dir: str,
        daily_data_dir: str,
        adj_factor_dir: str,
        est_window: int = 504,
        decay_halflife: int = 252,
    ):
        self.factor_data_dir = Path(factor_data_dir)
        self.daily_data_dir = Path(daily_data_dir)
        self.adj_factor_dir = Path(adj_factor_dir)
        self.est_window = est_window
        self.decay_halflife = decay_halflife

    # --- public ---

    def export_for_dates(self, rebalance_dates: list[str], output_dir: str,
                         force: bool = False) -> list[Path]:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        failed: list[str] = []
        for dt in rebalance_dates:
            json_path = out_path / f"barra_risk_{dt}.json"
            if json_path.exists() and not force:
                LOGGER.info("Skip existing: %s", json_path)
                written.append(json_path)
                continue
            try:
                payload = self._compute_risk_for_date(dt)
                if payload is None:
                    LOGGER.warning("No risk data for %s; skipping", dt)
                    failed.append(dt)
                    continue
                json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
                LOGGER.info("Wrote %s (%d symbols)", json_path, payload["n_symbols"])
                written.append(json_path)
            except Exception:
                LOGGER.exception("Failed month %s", dt)
                failed.append(dt)
                continue
        if failed:
            LOGGER.warning("%d month(s) failed: %s", len(failed), failed)
        return written

    def _compute_risk_for_date(self, as_of: str) -> dict | None:
        """Compute full Barra risk payload for a single as-of date.

        Loads trailing factor/return panel (no lookahead), runs cross-sectional
        regression to estimate factor covariance and specific variance, then
        loads as-of factor exposures and assembles the JSON payload. Returns
        None if any required stage yields no data.

        Args:
            as_of: Month-end date string ``YYYY-MM-DD``.
        """
        B_panel, r_panel = self._load_factor_and_returns(as_of, self.est_window)
        if B_panel is None or r_panel is None:
            return None
        result = self._cross_sectional_regression_panel(B_panel, r_panel)
        if result is None:
            return None
        f, residuals, all_symbols = result
        sigma_f = self._estimate_factor_cov(f)
        delta = self._estimate_specific_var(residuals, all_symbols)
        B_t = self._load_factor_exposure_asof(as_of)
        if B_t is None:
            LOGGER.warning("No as-of exposure for %s", as_of)
            return None
        # spec §4.1: full-market cross-section <30 symbols -> diagonal Sigma_f
        fallback = None
        if len(B_t) < 30:
            LOGGER.warning(
                "Only %d symbols with as-of exposure for %s (<30); "
                "falling back to diagonal Sigma_f", len(B_t), as_of)
            sigma_f = np.diag(np.diag(sigma_f))
            fallback = "diagonal"
        return {
            "as_of": as_of,
            "factors": FACTORS,
            "sigma_f": sigma_f.tolist(),
            "delta": {s: float(v) for s, v in delta.items() if not np.isnan(v)},
            "exposures": {s: [float(x) for x in row] for s, row in B_t.items()},
            "est_window": int(self.est_window),
            "decay_halflife": int(self.decay_halflife),
            "n_symbols": len(B_t),
            "n_obs": int(f.shape[0]),
            "fallback": fallback,
        }

    # --- regression ---

    def _cross_sectional_regression(self, B: np.ndarray, r: np.ndarray):
        """Per-date cross-sectional OLS: r_t = B_t·f_t + eps_t.
        B: (n_dates, n_symbols, n_factors), r: (n_dates, n_symbols).
        Returns (f_hat (n_dates, n_factors), residuals (n_dates, n_symbols)).
        Drops NaN symbols per date. Adds intercept column."""
        n_dates, n_symbols, n_factors = B.shape
        f_hat = np.full((n_dates, n_factors), np.nan)
        residuals = np.full((n_dates, n_symbols), np.nan)
        for t in range(n_dates):
            Bt = B[t]
            rt = r[t]
            mask = ~(np.isnan(Bt).any(axis=1) | np.isnan(rt))
            if mask.sum() < n_factors + 1:
                continue
            X = np.column_stack([np.ones(mask.sum()), Bt[mask]])
            y = rt[mask]
            try:
                coef, *_ = np.linalg.lstsq(X, y, rcond=None)
                f_hat[t] = coef[1:]
                residuals[t, mask] = y - X @ coef
            except np.linalg.LinAlgError:
                continue
        return f_hat, residuals

    def _cross_sectional_regression_panel(self, B_panel: dict, r_panel: pd.DataFrame):
        """Stack per-date factor exposures and returns, then run cross-sectional OLS.

        Aligns ``B_panel`` (dict[YYYYMMDD -> DataFrame[symbols x 15]]) with
        ``r_panel`` (DataFrame[dates x symbols]) on common dates, builds dense
        3-D arrays, and delegates to :meth:`_cross_sectional_regression`.

        Returns:
            ``(f_hat, residuals, all_symbols)`` or ``None`` if too few common
            dates survive.
        """
        common_dates = sorted(set(B_panel.keys()) & set(r_panel.index.strftime("%Y%m%d")))
        if len(common_dates) < self.est_window // 2:
            LOGGER.warning("Too few common dates (%d) for regression", len(common_dates))
            return None
        all_symbols = sorted({s for df in B_panel.values() for s in df.index}
                             | set(r_panel.columns))
        B_arr = np.full((len(common_dates), len(all_symbols), len(FACTORS)), np.nan)
        r_arr = np.full((len(common_dates), len(all_symbols)), np.nan)
        sym_idx = {s: i for i, s in enumerate(all_symbols)}
        for ti, d in enumerate(common_dates):
            df_b = B_panel[d]
            for s, row in df_b.iterrows():
                B_arr[ti, sym_idx[s]] = row[FACTORS].values.astype(float)
            r_series = r_panel.loc[pd.to_datetime(d, format="%Y%m%d")]
            for s, val in r_series.items():
                if s in sym_idx:
                    r_arr[ti, sym_idx[s]] = float(val) if not np.isnan(float(val)) else np.nan
        f_hat, residuals = self._cross_sectional_regression(B_arr, r_arr)
        valid = ~np.isnan(f_hat).all(axis=1)
        f_hat = f_hat[valid]
        residuals = residuals[valid]
        if len(f_hat) < self.est_window // 2:
            LOGGER.warning("Too few valid factor-return dates (%d)", len(f_hat))
            return None
        return f_hat, residuals, all_symbols

    # --- covariance estimation ---

    def _estimate_factor_cov(self, f: np.ndarray) -> np.ndarray:
        """Ledoit-Wolf shrinkage factor covariance + ridge, annualized (x252)."""
        f_clean = f[~np.isnan(f).any(axis=1)]
        if len(f_clean) < 2:
            LOGGER.warning("Too few factor-return rows for covariance (%d)", len(f_clean))
            return np.eye(len(FACTORS)) * 1e-4
        try:
            lw = LedoitWolf().fit(f_clean)
            cov = lw.covariance_
        except Exception:
            cov = np.cov(f_clean, rowvar=False)
        cov = np.atleast_2d(cov)
        cov = cov * 252.0
        cov += np.eye(cov.shape[0]) * 1e-8
        return cov

    def _estimate_specific_var(self, residuals: np.ndarray, symbols: list[str]) -> dict[str, float]:
        """Per-symbol exponential-decay variance of residuals, annualized (x252)."""
        n_dates, n_symbols = residuals.shape
        alpha = 1.0 - np.exp(-np.log(2) / self.decay_halflife)
        weights = (1 - alpha) ** np.arange(n_dates - 1, -1, -1)
        weights = weights / weights.sum()
        delta = {}
        for s_idx in range(n_symbols):
            ts_code = symbols[s_idx]
            col = residuals[:, s_idx]
            mask = ~np.isnan(col)
            idx = np.where(mask)[0]
            if len(idx) < 2:
                delta[ts_code] = float("nan")
                continue
            w = weights[idx]
            w = w / w.sum()
            valid = col[idx]
            mean = np.average(valid, weights=w)
            var = np.average((valid - mean) ** 2, weights=w)
            delta[ts_code] = float(max(var * 252.0, 1e-10))
        return delta

    # --- data loading ---

    def _load_factor_and_returns(self, as_of: str, window: int):
        """Load trailing factor exposures and back-adjusted returns strictly before as_of.

        Builds the estimation panel ``[as_of - window*1.6 days, as_of - 1]``
        (no lookahead: the as-of day itself is excluded). Factor exposures come
        from per-symbol CSVs under ``factor_data_dir``; close prices come from
        per-symbol daily parquet directories and are back-adjusted by adj_factor.

        Args:
            as_of: ``YYYY-MM-DD`` as-of date (excluded from the panel).
            window: Estimation window length in trading days.

        Returns:
            ``(B_panel, r_panel)`` where ``B_panel`` is dict[YYYYMMDD ->
            DataFrame[symbols x 15]] and ``r_panel`` is DataFrame[dates x
            symbols]; ``(None, None)`` if no data survives.
        """
        as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
        start_dt = as_of_dt - timedelta(days=int(window * 1.6))
        start_compact = start_dt.strftime("%Y%m%d")
        end_compact = (as_of_dt - timedelta(days=1)).strftime("%Y%m%d")  # no lookahead

        B_panel = {}
        for csv_path in sorted(self.factor_data_dir.rglob("*.csv")):
            ts_code = self._csv_to_tscode(csv_path)
            if ts_code is None:
                continue
            try:
                df = pd.read_csv(csv_path, dtype={"trade_date": str})
                df = df[(df["trade_date"] >= start_compact) & (df["trade_date"] <= end_compact)]
                if df.empty:
                    continue
                missing = [f for f in FACTORS if f not in df.columns]
                if missing:
                    LOGGER.warning("Skip %s: missing factor columns %s", ts_code, missing)
                    continue
                for _, row in df.iterrows():
                    d = row["trade_date"]
                    if d not in B_panel:
                        B_panel[d] = {}
                    B_panel[d][ts_code] = row[FACTORS].astype(float)
            except Exception:
                LOGGER.warning("Failed factor load for %s", ts_code)
                continue
        if not B_panel:
            return None, None
        B_panel_df = {d: pd.DataFrame.from_dict(expo, orient="index") for d, expo in B_panel.items()}

        # returns from daily parquet
        r_frames = {}
        for ts_dir in self.daily_data_dir.glob("ts_code=*"):
            ts_code = ts_dir.name[len("ts_code="):]
            try:
                df = pd.read_parquet(ts_dir)
                df["trade_date"] = df["trade_date"].astype(str)
                df = df[(df["trade_date"] >= start_compact) & (df["trade_date"] <= end_compact)]
                if df.empty:
                    continue
                df = df.sort_values("trade_date")
                adj = self._load_adj_factor(ts_code, start_compact, end_compact)
                if adj is None:
                    LOGGER.warning("Skip %s: adj_factor missing", ts_code)
                    continue
                if len(adj) != len(df):
                    LOGGER.warning("Skip %s: adj_factor len %d != daily len %d",
                                   ts_code, len(adj), len(df))
                    continue
                close = df["close"].astype(float).values * adj
                rets = pd.Series(np.diff(close) / close[:-1],
                                 index=df["trade_date"].values[1:])
                r_frames[ts_code] = rets
            except Exception:
                LOGGER.warning("Failed daily return load for %s", ts_code)
                continue
        if not r_frames:
            return None, None
        r_panel = pd.DataFrame(r_frames)
        r_panel.index = pd.to_datetime(r_panel.index, format="%Y%m%d")
        return B_panel_df, r_panel

    def _load_factor_exposure_asof(self, as_of: str) -> dict | None:
        """As-of factor exposures B_t for all symbols with data on the latest
        trade date <= as_of. Returns dict[ts_code -> np.array(15)]."""
        as_of_compact = as_of.replace("-", "")
        best_exposures = {}
        for csv_path in sorted(self.factor_data_dir.rglob("*.csv")):
            ts_code = self._csv_to_tscode(csv_path)
            if ts_code is None:
                continue
            try:
                df = pd.read_csv(csv_path, dtype={"trade_date": str})
                df = df[df["trade_date"] <= as_of_compact].sort_values("trade_date")
                if df.empty:
                    continue
                missing = [f for f in FACTORS if f not in df.columns]
                if missing:
                    LOGGER.warning("Skip %s: missing factor columns %s", ts_code, missing)
                    continue
                last = df.iloc[-1]
                best_exposures[ts_code] = last[FACTORS].astype(float).values
            except Exception:
                continue
        return best_exposures if best_exposures else None

    def _load_adj_factor(self, ts_code: str, start: str, end: str) -> np.ndarray | None:
        adj_dir = self.adj_factor_dir / f"ts_code={ts_code}"
        if not adj_dir.exists():
            return None
        try:
            df = pd.read_parquet(adj_dir)
            df["trade_date"] = df["trade_date"].astype(str)
            df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
            df = df.sort_values("trade_date")
            return df["adj_factor"].astype(float).values
        except Exception:
            LOGGER.warning("Failed adj_factor for %s", ts_code, exc_info=True)
            return None

    @staticmethod
    def _csv_to_tscode(csv_path: Path) -> str | None:
        """Map factor CSV path to ts_code. sse/ -> .SH, szse/ -> .SZ.
        Searches path parts for sse/szse (handles {sse,szse}/daily/<code>.csv layout)."""
        code = csv_path.stem
        if not code.isdigit():
            return None
        parts = [p.lower() for p in csv_path.parts]
        if "sse" in parts:
            return f"{code}.SH"
        if "szse" in parts:
            return f"{code}.SZ"
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Barra factor risk data")
    parser.add_argument("--start", required=True, help="First month-end (yyyy-MM-dd)")
    parser.add_argument("--end", required=True, help="Last month-end (yyyy-MM-dd)")
    parser.add_argument("--factor-dir", default=DEFAULT_FACTOR_DIR)
    parser.add_argument("--daily-dir", default=DEFAULT_DAILY_DIR)
    parser.add_argument("--adj-dir", default=DEFAULT_ADJ_DIR)
    parser.add_argument("--est-window", type=int, default=504)
    parser.add_argument("--decay-halflife", type=int, default=252)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    month_ends = _month_end_dates(args.start, args.end)
    LOGGER.info("Exporting Barra risk for %d month-ends", len(month_ends))
    exporter = BarraRiskExporter(
        factor_data_dir=args.factor_dir, daily_data_dir=args.daily_dir,
        adj_factor_dir=args.adj_dir, est_window=args.est_window,
        decay_halflife=args.decay_halflife)
    written = exporter.export_for_dates(month_ends, args.out_dir, force=args.force)
    LOGGER.info("Done: %d JSON files written to %s", len(written), args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
