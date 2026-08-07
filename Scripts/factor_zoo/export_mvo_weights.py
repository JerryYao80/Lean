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


class ICovarianceProvider:
    """Abstract covariance source for MVO. Decouples Sigma estimation from the
    optimizer — multiple strategies can share one MVO exporter with different
    Sigma sources (historical sample cov vs Barra structured cov)."""

    def estimate(self, symbols: list[str], as_of: str,
                 returns: pd.DataFrame | None = None) -> tuple[np.ndarray, dict]:
        """Return (sigma NxN, meta dict with 'cov_source')."""
        raise NotImplementedError


class HistoricalCovarianceProvider(ICovarianceProvider):
    """Ledoit-Wolf shrinkage sample covariance of trailing returns (P2-B default).
    Behavior identical to MVOWeightExporter._estimate_covariance."""

    def estimate(self, symbols: list[str], as_of: str,
                 returns: pd.DataFrame | None = None) -> tuple[np.ndarray, dict]:
        if returns is None:
            raise ValueError("HistoricalCovarianceProvider requires returns")
        arr = returns.values
        if np.isnan(arr).any():
            col_mean = np.nanmean(arr, axis=0)
            col_mean = np.where(np.isnan(col_mean), 0.0, col_mean)
            inds = np.where(np.isnan(arr))
            arr = arr.copy()
            arr[inds] = np.take(col_mean, inds[1])
        try:
            lw = LedoitWolf().fit(arr)
            cov = lw.covariance_
        except Exception:
            cov = np.cov(arr, rowvar=False)
        cov = np.atleast_2d(cov)
        cov = cov * 252.0
        cov += np.eye(cov.shape[0]) * 1e-8
        return cov, {"cov_source": "historical"}


class BarraCovarianceProvider(ICovarianceProvider):
    """Structured covariance from Barra factor risk: Sigma = B_t Sigma_f B_t' + diag(Delta).
    Reads barra_risk_YYYY-MM-DD.json produced by export_barra_risk.py.
    Loads latest file <= as_of (no lookahead — risk data estimated strictly before as_of)."""

    def __init__(self, barra_risk_dir: str):
        self.barra_risk_dir = Path(barra_risk_dir)

    def estimate(self, symbols: list[str], as_of: str,
                 returns: pd.DataFrame | None = None) -> tuple[np.ndarray, dict]:
        risk, risk_filename = self._load_latest_barra_risk(as_of)
        if risk is None:
            raise ValueError(f"No barra_risk data <= {as_of} in {self.barra_risk_dir}")
        sigma_f = np.array(risk["sigma_f"], dtype=float)
        delta_dict = risk.get("delta", {})
        expo_dict = risk.get("exposures", {})
        n = len(symbols)
        B = np.zeros((n, sigma_f.shape[0]))
        delta = np.zeros(n)
        n_missing_expo = 0
        n_missing_delta = 0
        available_deltas = [float(v) for v in delta_dict.values()
                            if not np.isnan(float(v))]
        # Conservative: a missing symbol should be treated as HIGH-risk (avoid it),
        # not zero-risk. Zero variance would make MVO concentrate max_weight into it.
        # Use the cross-sectional median of available deltas as the floor; if no
        # deltas are available at all, use a 0.05*252 annualized variance floor.
        delta_floor = (float(np.median(available_deltas))
                       if available_deltas else 0.05 * 252)
        for i, s in enumerate(symbols):
            if s in expo_dict:
                B[i] = np.array(expo_dict[s], dtype=float)
            else:
                n_missing_expo += 1
            if s in delta_dict and not np.isnan(float(delta_dict[s])):
                delta[i] = float(delta_dict[s])
            else:
                # conservative: treat missing as high-variance (floored to median)
                delta[i] = delta_floor
                n_missing_delta += 1
        if n_missing_expo > 0:
            LOGGER.warning(
                "BarraCovarianceProvider: %d/%d symbols missing factor exposure (zeroed)",
                n_missing_expo, n)
        if n_missing_delta > 0:
            LOGGER.warning(
                "BarraCovarianceProvider: %d/%d symbols missing specific variance "
                "(floored to median %.4f)", n_missing_delta, n, delta_floor)
        # A large fraction missing signals a systemic ticker-format mismatch
        # (not individual missing stocks) — raise rather than emit garbage.
        if n_missing_expo > n // 2:
            raise ValueError(
                f"BarraCovarianceProvider: {n_missing_expo}/{n} symbols missing "
                f"exposure — likely ticker-format mismatch")
        sigma = B @ sigma_f @ B.T + np.diag(delta)
        sigma += np.eye(n) * 1e-8
        return sigma, {
            "cov_source": "barra",
            "barra_risk_used": risk_filename,
        }

    def _load_latest_barra_risk(self, as_of: str) -> tuple[dict | None, str | None]:
        """Return (risk_dict, filename) for the latest barra_risk_*.json <= as_of.

        Returns (None, None) when no file <= as_of exists (legitimate "no data").
        A corrupt JSON file RAISES ValueError — masking it as "no data" would
        cause the caller to skip the month and the C# side to reuse stale
        weights (lookahead-in-disguise)."""
        if not self.barra_risk_dir.exists():
            return None, None
        as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
        best_dt = None
        best_path = None
        for p in self.barra_risk_dir.glob("barra_risk_*.json"):
            ds = p.stem[len("barra_risk_"):]
            try:
                dt = datetime.strptime(ds, "%Y-%m-%d")
            except ValueError:
                continue
            if dt <= as_of_dt and (best_dt is None or dt > best_dt):
                best_dt, best_path = dt, p
        if best_path is None:
            return None, None  # legitimate: no file <= as_of
        # Corrupt file: raise (do NOT mask as "no data" -> stale-weight reuse)
        try:
            return json.loads(best_path.read_text()), best_path.name
        except Exception as exc:
            raise ValueError(
                f"Corrupt barra_risk file {best_path}: {exc}") from exc


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
        cov_provider: ICovarianceProvider | None = None,
    ):
        self.ic_report_dir = Path(ic_report_dir)
        self.daily_data_dir = Path(daily_data_dir)
        self.adj_factor_dir = Path(adj_factor_dir)
        self.cov_window = cov_window
        self.max_weight = max_weight
        self.risk_aversion = risk_aversion
        self._panel_loader = FactorPanelLoader(factor_root) if factor_root else None
        self._cov_provider = cov_provider or HistoricalCovarianceProvider()

    # --- public ---

    def export_for_dates(self, rebalance_dates: list[str], output_dir: str,
                         force: bool = False) -> list[Path]:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        failed: list[str] = []
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
                    failed.append(dt)
                    continue
                json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
                LOGGER.info("Wrote %s (%d assets)", json_path, payload["n_assets"])
                written.append(json_path)
            except Exception:
                LOGGER.exception("Failed month %s", dt)
                failed.append(dt)
                continue
        if failed:
            LOGGER.warning("%d month(s) failed: %s", len(failed), failed)
        return written

    def _compute_weights_for_date(self, as_of: str) -> dict | None:
        alpha_scores, report_name = self._load_alpha_scores(as_of)
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
        ret = returns[symbols]

        # C2: Drop symbols with fewer than 60 non-NaN return observations.
        min_obs = 60
        obs_count = ret.notna().sum(axis=0)
        keep = obs_count[obs_count >= min_obs].index
        if len(keep) < len(symbols):
            LOGGER.info("Dropped %d symbols with < %d obs for %s",
                        len(symbols) - len(keep), min_obs, as_of)
        ret = ret[keep]
        # Re-align alpha to surviving symbols
        symbols = alpha.index.intersection(ret.columns)
        if len(symbols) < 2:
            LOGGER.warning("Too few symbols after min-obs filter (%d) for %s",
                           len(symbols), as_of)
            return None
        alpha = alpha[symbols]
        ret = ret[symbols]

        # Align on common dates. Use how="all" (drop a date only if NO symbol has
        # data) — how="any" would drop any date where a single stock was suspended,
        # which on a 300-stock universe over 120 days collapses to near-zero rows.
        # Per-symbol data quality is already enforced by the min_obs filter above;
        # remaining gaps are handled by Ledoit-Wolf shrinkage (designed for the
        # n < p / missing-data regime).
        ret = ret.dropna(axis=0, how="all")
        symbols = alpha.index.intersection(ret.columns)
        if len(symbols) < 2:
            LOGGER.warning("Too few symbols after common-date alignment for %s", as_of)
            return None
        alpha = alpha[symbols]
        ret = ret[symbols]

        # C2: Require enough observations for a well-conditioned covariance.
        # Fixed floor (NOT scaled by len(symbols)) — Ledoit-Wolf shrinkage
        # explicitly handles the p > n regime; a len(symbols)+1 threshold makes
        # MVO mathematically impossible for CSI300 (~300 symbols) with a 120-day
        # cov_window (~120 trading rows).
        min_rows = 60
        if len(ret) < min_rows:
            LOGGER.warning("Insufficient aligned rows (%d < %d) for %s; skipping",
                           len(ret), min_rows, as_of)
            return None

        mu = self._alpha_to_mu(alpha.values)
        sigma, cov_meta = self._cov_provider.estimate(symbols.tolist(), as_of, returns=ret)
        weights, fallback = self._solve_mvo(mu, sigma, symbols.tolist())
        # C3: infeasible -> empty weights dict -> skip
        if not weights:
            LOGGER.warning("MVO infeasible for %s (n*max_weight<1)", as_of)
            return None

        return {
            "as_of": as_of,
            "symbols": [{"ts_code": s, "weight": float(w)} for s, w in weights.items()],
            "sum": float(sum(weights.values())),
            "lambda": float(self.risk_aversion),
            "cov_window": int(self.cov_window),
            "n_assets": len(weights),
            "ic_report_used": report_name,
            "fallback": fallback,
            "cov_source": cov_meta.get("cov_source", "historical"),
            "barra_risk_used": cov_meta.get("barra_risk_used"),
        }

    # --- data loading (overridable in tests) ---

    def _load_alpha_scores(self, as_of: str) -> tuple[pd.Series | None, str | None]:
        """Load latest IC report <= as_of; synthesize per-stock alpha =
        sum(factor_value * factor_weight) / sum(weight), then cross-sectional
        normalize to [0,1] (mirrors C# ComputeAlphaScores).

        Returns (scores, report_name) tuple — report_name is the filename of
        the IC report actually used (None if no scores). Explicit return
        removes the prior temporal coupling via self._last_ic_report_name.
        """
        report, report_name = self._load_latest_ic_report(as_of)
        if report is None:
            return None, None
        if self._panel_loader is None:
            self._panel_loader = FactorPanelLoader()
        panel = self._panel_loader.load_panel(as_of)
        if panel is None or panel.empty:
            return None, report_name

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
        return scores, report_name

    def _load_latest_ic_report(self, as_of: str) -> tuple[dict | None, str | None]:
        """Return (report_dict, report_filename). Latest report with
        report_date <= as_of. No earliest-report fallback (would be a
        lookahead-in-disguise: using a future report's weights for a past
        date)."""
        if not self.ic_report_dir.exists():
            LOGGER.warning("IC report dir not found: %s", self.ic_report_dir)
            return None, None
        as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
        best_dt = None
        best_path = None
        for p in self.ic_report_dir.glob("ic_report_*.json"):
            ds = p.stem[len("ic_report_"):]
            try:
                dt = datetime.strptime(ds, "%Y-%m-%d")
            except ValueError:
                continue
            if dt <= as_of_dt and (best_dt is None or dt > best_dt):
                best_dt, best_path = dt, p
        if best_path is None:
            LOGGER.info("No IC report <= %s", as_of)
            return None, None
        try:
            return json.loads(best_path.read_text()), best_path.name
        except Exception:
            LOGGER.exception("Failed to parse %s", best_path)
            return None, None

    def _load_historical_returns(self, as_of: str, window: int) -> pd.DataFrame | None:
        """Trailing `window` back-adjusted daily returns STRICTLY BEFORE as_of."""
        as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
        start_dt = as_of_dt - timedelta(days=int(window * 1.6))  # calendar buffer
        start_compact = start_dt.strftime("%Y%m%d")
        end_compact = (as_of_dt - timedelta(days=1)).strftime("%Y%m%d")

        frames = {}
        n_total = 0
        n_failed = 0
        for ts_dir in self.daily_data_dir.glob("ts_code=*"):
            n_total += 1
            ts_code = ts_dir.name[len("ts_code="):]
            try:
                df = pd.read_parquet(ts_dir)
                df["trade_date"] = df["trade_date"].astype(str)
                df = df[(df["trade_date"] >= start_compact) & (df["trade_date"] <= end_compact)]
                if df.empty:
                    continue
                df = df.sort_values("trade_date")
                # C1: Back-adjusted close. If adj_factor is missing or
                # length-mismatched, SKIP the symbol — never silently fall
                # back to raw close (ex-div days would inject fake -10%
                # returns into the covariance).
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
                frames[ts_code] = rets
            except Exception:
                n_failed += 1
                LOGGER.debug("Failed to load daily for %s", ts_code, exc_info=True)
                continue
        if n_failed > 0:
            LOGGER.warning("Failed to load daily data for %d/%d symbols",
                           n_failed, n_total)
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
            LOGGER.warning("Failed to load adj_factor for %s", ts_code, exc_info=True)
            return None

    # --- MVO math ---

    def _alpha_to_mu(self, alpha: np.ndarray) -> np.ndarray:
        """Rank-percentile linear mapping to +/-20% annualized band."""
        pct = pd.Series(alpha).rank(pct=True).values
        return (pct - 0.5) * 0.40

    def _estimate_covariance(self, returns: pd.DataFrame) -> np.ndarray:
        """Delegate to the configured covariance provider (default historical).
        Kept for backward compatibility with P2-B regression tests. Robust to
        __new__ bypass (tests) — falls back to a fresh HistoricalCovarianceProvider
        when _cov_provider was never set."""
        provider = getattr(self, "_cov_provider", None) or HistoricalCovarianceProvider()
        sigma, _ = provider.estimate(returns.columns.tolist(), "", returns=returns)
        return sigma

    def _solve_mvo(self, mu: np.ndarray, sigma: np.ndarray,
                   symbols: list[str]) -> tuple[dict, str | None]:
        """SLSQP: min -mu'w + (lambda/2) w'Sigma w, s.t. sum(w)=1, 0<=w<=max.

        C3: If n*max_weight < 1 the constraints are infeasible (cannot sum
        to 1 with each w_i <= max). Returns ({}, "infeasible") so the caller
        can skip the date rather than emit weights violating the bound.
        """
        n = len(symbols)
        # C3: infeasibility detection
        if n * self.max_weight < 1.0 - 1e-9:
            LOGGER.warning("infeasible: n*max_weight<1 (n=%d, max=%s)", n, self.max_weight)
            return {}, "infeasible"
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

        # C3 defense-in-depth: clip to [0, max_weight] and renormalize
        # (2 passes). Guarantees the bound even if SLSQP or the equal-weight
        # fallback produced an out-of-bound value.
        for _ in range(2):
            w = np.clip(w, 0.0, self.max_weight)
            s = w.sum()
            if s > 0:
                w = w / s
            else:
                w = x0
                fallback = "equal_weight"
                break

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
    parser.add_argument("--covariance-source", choices=["historical", "barra"], default="historical")
    parser.add_argument("--barra-risk-dir", default="/home/project/hope/Lean/result/barra-risk")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    month_ends = _month_end_dates(args.start, args.end)
    LOGGER.info("Exporting MVO weights for %d month-ends", len(month_ends))

    if args.covariance_source == "barra":
        cov_provider = BarraCovarianceProvider(barra_risk_dir=args.barra_risk_dir)
    else:
        cov_provider = HistoricalCovarianceProvider()
    exporter = MVOWeightExporter(
        ic_report_dir=args.ic_report_dir, daily_data_dir=args.daily_dir,
        adj_factor_dir=args.adj_dir, cov_window=args.cov_window,
        max_weight=args.max_weight, risk_aversion=args.risk_aversion,
        cov_provider=cov_provider)
    written = exporter.export_for_dates(month_ends, args.out_dir, force=args.force)
    LOGGER.info("Done: %d JSON files written to %s", len(written), args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
