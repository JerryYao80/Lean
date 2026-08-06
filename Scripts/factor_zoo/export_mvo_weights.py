"""
export_mvo_weights.py — Offline expanding-window MVO weight generator.

For each month-end rebalance date t, computes mean-variance optimal weights:
  max  mu'w - (lambda/2) * w'Sigma w
  s.t. sum(w) = 1, 0 <= w_i <= max_weight

mu = rank-percentile linear mapping of alpha composite (factor_weight x factor_value
     synthesized from the latest IC report <= t, mirroring C# ComputeAlphaScores).
Sigma = Ledoit-Wolf-shrunk sample covariance of trailing `cov_window` back-adjusted
        daily returns strictly before t (no lookahead).

Output: result/mvo-weights/mvo_weights_YYYY-MM-DD.json
        (one file per month-end; C# MVOAlphaPortfolioConstructionModel reads
         the latest <= rebalance date at runtime.)

Reuses FactorPanelLoader (factor_panel_loader.py) + IC report JSON schema
from P2-A. Does NOT modify them.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

from factor_panel_loader import FactorPanelLoader

LOGGER = logging.getLogger("export_mvo_weights")

DEFAULT_DAILY_DIR = "/home/project/tushare-downloader/tushare_data_v2/daily"
DEFAULT_ADJ_DIR = "/home/project/tushare-downloader/tushare_data_v2/adj_factor"
DEFAULT_OUT_DIR = "/home/project/hope/Lean/result/mvo-weights"
DEFAULT_IC_REPORT_DIR = "/home/project/hope/Lean/result/ic-reports"


def _month_end_dates(start: str, end: str) -> list[str]:
    """All month-end dates in [start, end]; trailing partial month skipped."""
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


class MVOWeightExporter:
    """Offline MVO weight exporter (expanding-window, no lookahead)."""

    def __init__(
        self,
        ic_report_dir: str,
        daily_data_dir: str,
        adj_factor_dir: str,
        cov_window: int = 120,
        max_weight: float = 0.10,
        risk_aversion: float = 1.0,
        factor_root: str | None = None,
    ):
        self.ic_report_dir = Path(ic_report_dir)
        self.daily_data_dir = Path(daily_data_dir)
        self.adj_factor_dir = Path(adj_factor_dir)
        self.cov_window = cov_window
        self.max_weight = max_weight
        self.risk_aversion = risk_aversion
        self._panel_loader = FactorPanelLoader(factor_root) if factor_root else None
        self._ic_cache: dict[str, dict] = {}

    # --- public ---

    def export_for_dates(self, rebalance_dates: list[str], output_dir: str,
                         force: bool = False) -> list[Path]:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for dt in rebalance_dates:
            json_path = out_path / f"mvo_weights_{dt}.json"
            if json_path.exists() and not force:
                LOGGER.info("Skip existing: %s", json_path)
                written.append(json_path)
                continue
            try:
                payload = self._compute_weights_for_date(dt)
                if payload is None:
                    LOGGER.warning("No weights for %s; skipping", dt)
                    continue
                json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
                LOGGER.info("Wrote %s (%d assets)", json_path, payload["n_assets"])
                written.append(json_path)
            except Exception:
                LOGGER.exception("Failed month %s", dt)
                continue
        return written

    def _compute_weights_for_date(self, as_of: str) -> dict | None:
        alpha_scores = self._load_alpha_scores(as_of)
        if alpha_scores is None or alpha_scores.empty:
            LOGGER.warning("No alpha scores for %s", as_of)
            return None
        returns = self._load_historical_returns(as_of, self.cov_window)
        if returns is None or returns.empty:
            LOGGER.warning("No historical returns for %s", as_of)
            return None

        symbols = alpha_scores.index.intersection(returns.columns)
        if len(symbols) < 2:
            LOGGER.warning("Too few overlapping symbols (%d) for %s", len(symbols), as_of)
            return None
        alpha = alpha_scores[symbols]
        ret = returns[symbols].dropna(axis=1, how="all").dropna(axis=0, how="any")
        # Re-align after dropna
        symbols = alpha.index.intersection(ret.columns)
        if len(symbols) < 2:
            return None
        alpha = alpha[symbols]
        ret = ret[symbols]

        mu = self._alpha_to_mu(alpha.values)
        sigma = self._estimate_covariance(ret)
        weights, fallback = self._solve_mvo(mu, sigma, symbols.tolist())

        return {
            "as_of": as_of,
            "symbols": [{"ts_code": s, "weight": float(w)} for s, w in weights.items()],
            "sum": float(sum(weights.values())),
            "lambda": float(self.risk_aversion),
            "cov_window": int(self.cov_window),
            "n_assets": len(weights),
            "ic_report_used": self._last_ic_report_name,
            "fallback": fallback,
        }

    # --- data loading (overridable in tests) ---

    _last_ic_report_name: str | None = None

    def _load_alpha_scores(self, as_of: str) -> pd.Series | None:
        """Load latest IC report <= as_of; synthesize per-stock alpha =
        sum(factor_value * factor_weight) / sum(weight), then cross-sectional
        normalize to [0,1] (mirrors C# ComputeAlphaScores)."""
        report = self._load_latest_ic_report(as_of)
        if report is None:
            return None
        if self._panel_loader is None:
            self._panel_loader = FactorPanelLoader()
        panel = self._panel_loader.load_panel(as_of)
        if panel is None or panel.empty:
            return None

        factors = report.get("factors", [])
        scores = pd.Series(0.0, index=panel.index, dtype=float)
        total_w = 0.0
        for f in factors:
            fid = f["factor_id"]
            w = f["weight"]
            if fid in panel.columns:
                scores = scores.add(panel[fid].fillna(0) * w, fill_value=0)
                total_w += w
        if total_w > 0:
            scores = scores / total_w
        # Cross-sectional normalize to [0,1]
        lo, hi = scores.min(), scores.max()
        if hi > lo:
            scores = (scores - lo) / (hi - lo)
        return scores

    def _load_latest_ic_report(self, as_of: str) -> dict | None:
        if not self.ic_report_dir.exists():
            LOGGER.warning("IC report dir not found: %s", self.ic_report_dir)
            return None
        as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
        best_dt = None
        best_path = None
        earliest_dt = None
        earliest_path = None
        for p in self.ic_report_dir.glob("ic_report_*.json"):
            ds = p.stem[len("ic_report_"):]
            try:
                dt = datetime.strptime(ds, "%Y-%m-%d")
            except ValueError:
                continue
            if earliest_dt is None or dt < earliest_dt:
                earliest_dt, earliest_path = dt, p
            if dt <= as_of_dt and (best_dt is None or dt > best_dt):
                best_dt, best_path = dt, p
        path = best_path or earliest_path
        if path is None:
            return None
        self._last_ic_report_name = path.name
        try:
            return json.loads(path.read_text())
        except Exception:
            LOGGER.exception("Failed to parse %s", path)
            return None

    def _load_historical_returns(self, as_of: str, window: int) -> pd.DataFrame | None:
        """Trailing `window` back-adjusted daily returns STRICTLY BEFORE as_of."""
        as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
        start_dt = as_of_dt - timedelta(days=int(window * 1.6))  # calendar buffer
        start_compact = start_dt.strftime("%Y%m%d")
        end_compact = (as_of_dt - timedelta(days=1)).strftime("%Y%m%d")

        frames = {}
        for ts_dir in self.daily_data_dir.glob("ts_code=*"):
            ts_code = ts_dir.name[len("ts_code="):]
            try:
                df = pd.read_parquet(ts_dir)
                df["trade_date"] = df["trade_date"].astype(str)
                df = df[(df["trade_date"] >= start_compact) & (df["trade_date"] <= end_compact)]
                if df.empty:
                    continue
                df = df.sort_values("trade_date")
                # Back-adjusted close
                adj = self._load_adj_factor(ts_code, start_compact, end_compact)
                close = df["close"].astype(float).values
                if adj is not None and len(adj) == len(df):
                    close = close * adj
                rets = pd.Series(np.diff(close) / close[:-1],
                                 index=df["trade_date"].values[1:])
                frames[ts_code] = rets
            except Exception:
                LOGGER.debug("Failed to load daily for %s", ts_code, exc_info=True)
                continue
        if not frames:
            return None
        panel = pd.DataFrame(frames)
        panel.index = pd.to_datetime(panel.index, format="%Y%m%d")
        return panel.tail(window)

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
            return None

    # --- MVO math ---

    def _alpha_to_mu(self, alpha: np.ndarray) -> np.ndarray:
        """Rank-percentile linear mapping to +/-20% annualized band."""
        pct = pd.Series(alpha).rank(pct=True).values
        return (pct - 0.5) * 0.40

    def _estimate_covariance(self, returns: pd.DataFrame) -> np.ndarray:
        """Ledoit-Wolf shrinkage covariance + tiny ridge for PD stability."""
        try:
            lw = LedoitWolf().fit(returns.values)
            cov = lw.covariance_
        except Exception:
            cov = np.cov(returns.values, rowvar=False)
        cov = np.atleast_2d(cov)
        cov += np.eye(cov.shape[0]) * 1e-8
        return cov

    def _solve_mvo(self, mu: np.ndarray, sigma: np.ndarray,
                   symbols: list[str]) -> tuple[dict, str | None]:
        """SLSQP: min -mu'w + (lambda/2) w'Sigma w, s.t. sum(w)=1, 0<=w<=max."""
        n = len(symbols)
        x0 = np.full(n, 1.0 / n)

        def objective(w):
            return -np.dot(mu, w) + 0.5 * self.risk_aversion * np.dot(w, np.dot(sigma, w))

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        bounds = [(0.0, self.max_weight) for _ in range(n)]
        fallback = None
        try:
            res = minimize(objective, x0, method="SLSQP", bounds=bounds,
                           constraints=constraints,
                           options={"ftol": 1e-9, "maxiter": 1000})
            if not res.success:
                LOGGER.warning("SLSQP did not converge: %s — equal-weight fallback", res.message)
                w = x0.copy()
                fallback = "equal_weight"
            else:
                w = res.x
        except Exception:
            LOGGER.exception("MVO solve failed — equal-weight fallback")
            w = x0.copy()
            fallback = "equal_weight"

        w = np.where(w < 1e-6, 0, w)
        s = w.sum()
        if s > 0:
            w = w / s
        else:
            w = x0
            fallback = "equal_weight"
        return {sym: float(wi) for sym, wi in zip(symbols, w)}, fallback


def main() -> int:
    parser = argparse.ArgumentParser(description="Export expanding-window MVO weights")
    parser.add_argument("--start", required=True, help="First month-end (yyyy-MM-dd)")
    parser.add_argument("--end", required=True, help="Last month-end (yyyy-MM-dd)")
    parser.add_argument("--ic-report-dir", default=DEFAULT_IC_REPORT_DIR)
    parser.add_argument("--daily-dir", default=DEFAULT_DAILY_DIR)
    parser.add_argument("--adj-dir", default=DEFAULT_ADJ_DIR)
    parser.add_argument("--cov-window", type=int, default=120)
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--risk-aversion", type=float, default=1.0)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    month_ends = _month_end_dates(args.start, args.end)
    LOGGER.info("Exporting MVO weights for %d month-ends", len(month_ends))

    exporter = MVOWeightExporter(
        ic_report_dir=args.ic_report_dir, daily_data_dir=args.daily_dir,
        adj_factor_dir=args.adj_dir, cov_window=args.cov_window,
        max_weight=args.max_weight, risk_aversion=args.risk_aversion)
    written = exporter.export_for_dates(month_ends, args.out_dir, force=args.force)
    LOGGER.info("Done: %d JSON files written to %s", len(written), args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
