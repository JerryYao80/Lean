"""Barra CNE5 V2 factor builder — extends V1 with 5 new factors (10 descriptors).

Adds moneyflow, quality, northbound, margin, chipcost factors derived from
12 strategy families, computed with proper Barra methodology:
- Weighted mean with exponential decay (half-life)
- Winsorize z-score standardization
- Cross-sectional orthogonalization
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from barra_cne5_data_loader import BarraCNE5DataLoader
from barra_cne5_factor_builder import (
    BarraCNE5FactorBuilder,
    FACTOR_COMPOSITION,
    DESCRIPTOR_COLUMNS,
)


FACTOR_COMPOSITION_V2 = {
    # 10 original (EPIBS/EGIBS/EGIBS_s removed, weights renormalized)
    "beta": {"BETA": 1.00},
    "momentum": {"RSTR": 1.00},
    "size": {"LNCAP": 1.00},
    "earnyld": {"ETOP": 0.35, "CETOP": 0.65},
    "resvol": {"DASTD": 0.74, "CMRA": 0.16, "HSIGMA": 0.10},
    "growth": {"SGRO": 0.43, "EGRO": 0.57},
    "btop": {"BTOP": 1.00},
    "leverage": {"MLEV": 0.38, "DTOA": 0.35, "BLEV": 0.27},
    "liquidity": {"STOM": 0.35, "STOQ": 0.35, "STOA": 0.30},
    "nlsize": {"NLSIZE": 1.00},
    # 5 new V2
    "moneyflow": {"MFNET": 0.50, "BFRATIO": 0.50},
    "quality": {"ROE": 0.40, "GPM": 0.30, "DTA": 0.30},
    "northbound": {"HKRATIO": 0.50, "NMFLOW": 0.50},
    "margin": {"MGRATIO": 0.50, "MGCHANGE": 0.50},
    "chipcost": {"WINRATE": 0.50, "CSPREAD": 0.50},
}

DESCRIPTOR_COLUMNS_V2 = [
    *DESCRIPTOR_COLUMNS,
    "MFNET", "BFRATIO",
    "ROE", "GPM", "DTA",
    "HKRATIO", "NMFLOW",
    "MGRATIO", "MGCHANGE",
    "WINRATE", "CSPREAD",
]

FACTOR_COLUMNS_V2 = list(FACTOR_COMPOSITION_V2.keys())

V2_DESCRIPTOR_NAMES = [
    "MFNET", "BFRATIO",
    "ROE", "GPM", "DTA",
    "HKRATIO", "NMFLOW",
    "MGRATIO", "MGCHANGE",
    "WINRATE", "CSPREAD",
]


class BarraCNE5V2FactorBuilder(BarraCNE5FactorBuilder):
    """Extends V1 builder with 5 new factors (10 new descriptors).

    Inheritance strategy:
    - _build_symbol_row(): call super(), then compute 10 new descriptors
    - _compose_and_standardize(): call super() for original 10 factors,
      then standardize new descriptors, compose 5 new factors,
      and apply orthogonalization (moneyflow vs liquidity, margin vs leverage)
    """

    def _build_symbol_row(self, ts_code: str, trade_date: str, market_symbol: str) -> dict | None:
        row = super()._build_symbol_row(ts_code, trade_date, market_symbol)
        if row is None:
            return None

        new_descriptors = {
            "MFNET": math.nan, "BFRATIO": math.nan,
            "ROE": math.nan, "GPM": math.nan, "DTA": math.nan,
            "HKRATIO": math.nan, "NMFLOW": math.nan,
            "MGRATIO": math.nan, "MGCHANGE": math.nan,
            "WINRATE": math.nan, "CSPREAD": math.nan,
        }

        # Money flow descriptors
        mfnet = self._compute_mfnet(ts_code, trade_date)
        if mfnet is not None and math.isfinite(mfnet):
            new_descriptors["MFNET"] = mfnet

        bfratio = self._compute_bfratio(ts_code, trade_date)
        if bfratio is not None and math.isfinite(bfratio):
            new_descriptors["BFRATIO"] = bfratio

        # Quality descriptors (PIT financial data)
        quality = self._compute_quality_descriptors(ts_code, trade_date)
        for key, value in quality.items():
            if value is not None and math.isfinite(value):
                new_descriptors[key] = value

        # Northbound descriptors
        hkratio = self._compute_hkratio(ts_code, trade_date)
        if hkratio is not None and math.isfinite(hkratio):
            new_descriptors["HKRATIO"] = hkratio

        nmflow = self._compute_nmflow(trade_date)
        if nmflow is not None and math.isfinite(nmflow):
            new_descriptors["NMFLOW"] = nmflow

        # Margin descriptors
        margin = self._compute_margin_descriptors(ts_code, trade_date)
        for key, value in margin.items():
            if value is not None and math.isfinite(value):
                new_descriptors[key] = value

        # Chip cost descriptors
        chip = self._compute_chipcost_descriptors(ts_code, trade_date)
        for key, value in chip.items():
            if value is not None and math.isfinite(value):
                new_descriptors[key] = value

        row.update(new_descriptors)
        return row

    def build_factor_snapshot(
        self,
        universe: list[str],
        trade_date: str,
        market_symbol: str = "000300.SH",
        progress_callback=None,
        progress_interval: int | None = None,
    ) -> pd.DataFrame:
        rows: list[dict] = []
        total_symbols = len(universe)
        report_every = max(1, int(progress_interval or 100))
        started_at = time.perf_counter()
        for index, ts_code in enumerate(universe, start=1):
            row = self._build_symbol_row(ts_code=ts_code, trade_date=trade_date, market_symbol=market_symbol)
            if row is not None:
                rows.append(row)
            if progress_callback and (index == 1 or index % report_every == 0 or index == total_symbols):
                progress_callback({
                    "stage": "build_snapshot",
                    "trade_date": trade_date,
                    "completed": index,
                    "total": total_symbols,
                    "accepted": len(rows),
                    "current_symbol": ts_code,
                    "elapsed_seconds": time.perf_counter() - started_at,
                })

        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(columns=[
                "ts_code",
                "trade_date",
                *FACTOR_COLUMNS_V2,
                "total_mv",
                "turnover_rate",
                "listed_days",
                "missing_factor_count",
                "is_st",
            ])

        frame = self._compose_and_standardize(frame)
        factor_values = frame[FACTOR_COLUMNS_V2]
        frame["missing_factor_count"] = factor_values.isna().sum(axis=1).astype(int)

        output = frame[[
            "ts_code",
            "trade_date",
            *FACTOR_COLUMNS_V2,
            "total_mv",
            "turnover_rate",
            "listed_days",
            "missing_factor_count",
            "is_st",
        ]].copy()
        return output.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    def _compose_and_standardize(self, frame: pd.DataFrame) -> pd.DataFrame:
        # First run V1 standardization for original 18 descriptors and 10 factors
        # We need to handle V2 earnyld weights, so we override composition
        data = frame.copy()

        # Standardize all V1 descriptors (except NLSIZE)
        v1_descriptors = [c for c in DESCRIPTOR_COLUMNS if c != "NLSIZE"]
        for descriptor in v1_descriptors:
            data[descriptor] = self._winsorize_zscore(data[descriptor])

        # NLSIZE: cubic residual of LNCAP
        raw_lncap = self._numeric(frame["LNCAP"])
        data["NLSIZE"] = self._winsorize_zscore(
            self._cross_section_residual(raw_lncap.pow(3), raw_lncap)
        )

        # Compose original 10 factors with V2 weights
        original_factors = ["beta", "momentum", "size", "earnyld", "resvol",
                           "growth", "btop", "leverage", "liquidity", "nlsize"]
        for factor_name in original_factors:
            composition = FACTOR_COMPOSITION_V2[factor_name]
            data[factor_name] = self._compose_factor(data, composition)
            data[factor_name] = self._winsorize_zscore(data[factor_name])

        # Orthogonalize resvol against beta
        data["resvol"] = self._winsorize_zscore(
            self._cross_section_residual(data["resvol"], data["beta"])
        )

        # Standardize V2 descriptors
        for descriptor in V2_DESCRIPTOR_NAMES:
            data[descriptor] = self._winsorize_zscore(data[descriptor])

        # Compose 5 new V2 factors
        v2_factor_names = ["moneyflow", "quality", "northbound", "margin", "chipcost"]
        for factor_name in v2_factor_names:
            composition = FACTOR_COMPOSITION_V2[factor_name]
            data[factor_name] = self._compose_factor(data, composition)
            data[factor_name] = self._winsorize_zscore(data[factor_name])

        # Orthogonalize moneyflow against liquidity
        data["moneyflow"] = self._winsorize_zscore(
            self._cross_section_residual(data["moneyflow"], data["liquidity"])
        )

        # Orthogonalize margin against leverage
        data["margin"] = self._winsorize_zscore(
            self._cross_section_residual(data["margin"], data["leverage"])
        )

        return data

    # --- V2 descriptor computation methods --- #

    def _compute_mfnet(self, ts_code: str, trade_date: str) -> float | None:
        """MFNET: Weighted mean of net_mf_amount/circ_mv over 30-day window."""
        moneyflow = self.loader.load_dataset(
            "moneyflow",
            symbol=ts_code,
            end_date=trade_date,
            fields=["trade_date", "net_mf_amount"],
        )
        if moneyflow.empty:
            return None

        daily_basic = self.loader.load_dataset(
            "daily_basic",
            symbol=ts_code,
            end_date=trade_date,
            fields=["trade_date", "circ_mv"],
        )
        if daily_basic.empty:
            return None

        mf = moneyflow.sort_values("trade_date").tail(30)
        db = daily_basic.sort_values("trade_date").tail(30)

        merged = mf.merge(db, on="trade_date", how="inner")
        if merged.empty:
            return None

        net_mf = self._numeric(merged["net_mf_amount"])
        circ = self._numeric(merged["circ_mv"])
        ratio = net_mf / circ.where(circ > 0)
        ratio = ratio.replace([np.inf, -np.inf], np.nan)

        valid = ratio.dropna()
        if len(valid) < 5:
            return None

        return self._weighted_mean(valid.values, half_life=10)

    def _compute_bfratio(self, ts_code: str, trade_date: str) -> float | None:
        """BFRATIO: Weighted mean of (buy_lg - sell_lg) / amount over 30-day window."""
        moneyflow = self.loader.load_dataset(
            "moneyflow",
            symbol=ts_code,
            end_date=trade_date,
            fields=["trade_date", "buy_lg_amount", "sell_lg_amount"],
        )
        if moneyflow.empty:
            return None

        daily = self.loader.load_dataset(
            "daily",
            symbol=ts_code,
            end_date=trade_date,
            fields=["trade_date", "amount"],
        )
        if daily.empty:
            return None

        mf = moneyflow.sort_values("trade_date").tail(30)
        dd = daily.sort_values("trade_date").tail(30)

        merged = mf.merge(dd, on="trade_date", how="inner")
        if merged.empty:
            return None

        buy_lg = self._numeric(merged["buy_lg_amount"])
        sell_lg = self._numeric(merged["sell_lg_amount"])
        amount = self._numeric(merged["amount"])
        net_big = buy_lg - sell_lg
        ratio = net_big / amount.where(amount > 0)
        ratio = ratio.replace([np.inf, -np.inf], np.nan)

        valid = ratio.dropna()
        if len(valid) < 5:
            return None

        return self._weighted_mean(valid.values, half_life=10)

    def _compute_quality_descriptors(self, ts_code: str, trade_date: str) -> dict[str, float | None]:
        """ROE, GPM, DTA from fina_indicator (point-in-time)."""
        result = {"ROE": None, "GPM": None, "DTA": None}

        latest = self.loader.load_point_in_time(
            "fina_indicator",
            symbol=ts_code,
            asof_date=trade_date,
            fields=["roe", "grossprofit_margin", "debt_to_assets"],
        )
        if latest is None:
            return result

        roe_val = self._to_float(latest.get("roe"))
        gpm_val = self._to_float(latest.get("grossprofit_margin"))
        dta_val = self._to_float(latest.get("debt_to_assets"))

        if roe_val is not None and math.isfinite(roe_val):
            result["ROE"] = roe_val
        if gpm_val is not None and math.isfinite(gpm_val):
            result["GPM"] = gpm_val
        if dta_val is not None and math.isfinite(dta_val):
            result["DTA"] = -dta_val  # Inverted: low debt = high quality

        return result

    def _compute_hkratio(self, ts_code: str, trade_date: str) -> float | None:
        """HKRATIO: hk_hold.ratio at latest available date."""
        hk = self.loader.load_dataset(
            "hk_hold",
            symbol=ts_code,
            end_date=trade_date,
            fields=["trade_date", "ratio"],
        )
        if hk.empty:
            return None

        hk = hk.sort_values("trade_date")
        latest_row = hk.iloc[-1]
        ratio = self._to_float(latest_row.get("ratio"))
        if ratio is None or not math.isfinite(ratio):
            return None
        return ratio

    def _compute_nmflow(self, trade_date: str) -> float | None:
        """NMFLOW: Weighted mean of moneyflow_hsgt.north_money over 30-day window."""
        hsgt = self.loader.load_dataset(
            "moneyflow_hsgt",
            end_date=trade_date,
            fields=["trade_date", "north_money"],
        )
        if hsgt.empty:
            return None

        hsgt = hsgt.sort_values("trade_date").tail(30)
        nm = self._numeric(hsgt["north_money"])
        valid = nm.dropna()
        if len(valid) < 5:
            return None

        return self._weighted_mean(valid.values, half_life=10)

    def _compute_margin_descriptors(self, ts_code: str, trade_date: str) -> dict[str, float | None]:
        """MGRATIO and MGCHANGE from margin_detail."""
        result = {"MGRATIO": None, "MGCHANGE": None}

        # MGRATIO: current margin buy ratio
        margin = self.loader.load_dataset(
            "margin_detail",
            symbol=ts_code,
            end_date=trade_date,
            fields=["trade_date", "rzye", "rqye", "rzmre"],
        )
        if margin.empty:
            return result

        margin = margin.sort_values("trade_date")
        latest_row = margin.iloc[-1]
        rzmre = self._to_float(latest_row.get("rzmre"))
        rqye = self._to_float(latest_row.get("rqye"))

        if rzmre is not None and rqye is not None:
            denom = rzmre + rqye
            if denom > 0 and math.isfinite(denom):
                result["MGRATIO"] = rzmre / denom

        # MGCHANGE: weighted mean of log-changes of rzye
        rzye_series = self._numeric(margin.set_index("trade_date")["rzye"]).tail(30)
        if len(rzye_series) >= 5:
            rzye_vals = rzye_series.values.astype(float)
            valid_mask = np.isfinite(rzye_vals)
            if valid_mask.sum() >= 5:
                rzye_clean = rzye_vals[valid_mask]
                if len(rzye_clean) >= 3 and rzye_clean[0] > 0:
                    log_changes = np.log(rzye_clean[1:] / rzye_clean[:-1])
                    valid_lc = log_changes[np.isfinite(log_changes)]
                    if len(valid_lc) >= 2:
                        result["MGCHANGE"] = self._weighted_mean(valid_lc, half_life=10)

        return result

    def _compute_chipcost_descriptors(self, ts_code: str, trade_date: str) -> dict[str, float | None]:
        """WINRATE and CSPREAD from cyq_perf."""
        result = {"WINRATE": None, "CSPREAD": None}

        cyq = self.loader.load_dataset(
            "cyq_perf",
            symbol=ts_code,
            end_date=trade_date,
            fields=["trade_date", "winner_rate", "cost_5pct", "cost_50pct", "cost_95pct"],
        )
        if cyq.empty:
            return result

        cyq = cyq.sort_values("trade_date")
        latest_row = cyq.iloc[-1]

        winrate = self._to_float(latest_row.get("winner_rate"))
        if winrate is not None and math.isfinite(winrate):
            result["WINRATE"] = winrate

        cost_5 = self._to_float(latest_row.get("cost_5pct"))
        cost_50 = self._to_float(latest_row.get("cost_50pct"))
        cost_95 = self._to_float(latest_row.get("cost_95pct"))

        if cost_50 is not None and cost_50 > 0 and math.isfinite(cost_50):
            spread = cost_95 - cost_5
            if spread is not None and math.isfinite(spread):
                result["CSPREAD"] = -spread / cost_50  # Inverted: tight = high score

        return result