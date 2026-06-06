from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from barra_cne5_data_loader import BarraCNE5DataLoader


FACTOR_COMPOSITION = {
    "beta": {"BETA": 1.00},
    "momentum": {"RSTR": 1.00},
    "size": {"LNCAP": 1.00},
    "earnyld": {"ETOP": 0.34, "CETOP": 0.66},
    "resvol": {"DASTD": 0.74, "CMRA": 0.16, "HSIGMA": 0.10},
    "growth": {"SGRO": 0.43, "EGRO": 0.57},
    "btop": {"BTOP": 1.00},
    "leverage": {"MLEV": 0.38, "DTOA": 0.35, "BLEV": 0.27},
    "liquidity": {"STOM": 0.35, "STOQ": 0.35, "STOA": 0.30},
    "nlsize": {"NLSIZE": 1.00},
}

DESCRIPTOR_COLUMNS = [
    "BETA",
    "RSTR",
    "LNCAP",
    "ETOP",
    "CETOP",
    "DASTD",
    "CMRA",
    "HSIGMA",
    "SGRO",
    "EGRO",
    "BTOP",
    "MLEV",
    "DTOA",
    "BLEV",
    "STOM",
    "STOQ",
    "STOA",
    "NLSIZE",
]

FACTOR_COLUMNS = list(FACTOR_COMPOSITION.keys())


class BarraCNE5FactorBuilder:
    def __init__(self, tushare_data_path: str | Path, loader: BarraCNE5DataLoader | None = None):
        self.data_root = Path(tushare_data_path)
        self.loader = loader or BarraCNE5DataLoader(tushare_data_path)
        self._stock_basic_lookup: dict[str, tuple[str, str]] | None = None

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
                *FACTOR_COLUMNS,
                "total_mv",
                "turnover_rate",
                "listed_days",
                "missing_factor_count",
                "is_st",
            ])

        frame = self._compose_and_standardize(frame)
        factor_values = frame[FACTOR_COLUMNS]
        frame["missing_factor_count"] = factor_values.isna().sum(axis=1).astype(int)

        output = frame[[
            "ts_code",
            "trade_date",
            *FACTOR_COLUMNS,
            "total_mv",
            "turnover_rate",
            "listed_days",
            "missing_factor_count",
            "is_st",
        ]].copy()
        return output.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    def write_factor_history(
        self,
        frame: pd.DataFrame,
        output_root: str | Path,
        progress_callback=None,
        progress_interval: int | None = None,
    ) -> dict[str, Path]:
        output_root = Path(output_root)
        written: dict[str, Path] = {}

        if frame.empty:
            return written

        total_symbols = int(frame["ts_code"].nunique())
        report_every = max(1, int(progress_interval or 100))
        started_at = time.perf_counter()
        for index, (ts_code, group) in enumerate(frame.groupby("ts_code", sort=True), start=1):
            ticker, market = ts_code.split(".")
            market_directory = "sse" if market == "SH" else "szse"
            file_path = output_root / market_directory / "daily" / f"{ticker}.csv"
            file_path.parent.mkdir(parents=True, exist_ok=True)

            symbol_frame = group.drop(columns=["ts_code"]).copy()
            if file_path.exists():
                existing = pd.read_csv(file_path, dtype={"trade_date": str})
                symbol_frame = pd.concat([existing, symbol_frame], ignore_index=True)
                symbol_frame["trade_date"] = symbol_frame["trade_date"].astype(str).str.zfill(8)
                symbol_frame = symbol_frame.drop_duplicates(subset=["trade_date"], keep="last")

            symbol_frame = symbol_frame.sort_values("trade_date").reset_index(drop=True)
            symbol_frame.to_csv(file_path, index=False, float_format="%.10f")
            written[ts_code] = file_path
            if progress_callback and (index == 1 or index % report_every == 0 or index == total_symbols):
                progress_callback({
                    "stage": "write_factor_history",
                    "completed": index,
                    "total": total_symbols,
                    "ts_code": ts_code,
                    "file_path": str(file_path),
                    "rows": int(len(symbol_frame)),
                    "elapsed_seconds": time.perf_counter() - started_at,
                })

        return written

    def _build_symbol_row(self, ts_code: str, trade_date: str, market_symbol: str) -> dict | None:
        total_mv, turnover_rate = self._load_market_cap_and_turnover(ts_code, trade_date)
        listed_days, is_st = self._load_stock_flags(ts_code, trade_date)
        if total_mv is None:
            return None

        descriptors = {
            "BETA": math.nan,
            "RSTR": math.nan,
            "LNCAP": math.nan,
            "ETOP": math.nan,
            "CETOP": math.nan,
            "DASTD": math.nan,
            "CMRA": math.nan,
            "HSIGMA": math.nan,
            "SGRO": math.nan,
            "EGRO": math.nan,
            "BTOP": math.nan,
            "MLEV": math.nan,
            "DTOA": math.nan,
            "BLEV": math.nan,
            "STOM": math.nan,
            "STOQ": math.nan,
            "STOA": math.nan,
        }

        aligned_long = self._aligned_return_frame(ts_code, trade_date, market_symbol, lookback=525)
        aligned_short = aligned_long.tail(252).reset_index(drop=True) if not aligned_long.empty else aligned_long
        income = self.loader.load_dataset(
            "income",
            symbol=ts_code,
            fields=["end_date", "f_ann_date", "ann_date", "revenue", "n_income_attr_p"],
        )
        cashflow = self.loader.load_dataset(
            "cashflow",
            symbol=ts_code,
            fields=["end_date", "f_ann_date", "ann_date", "depr_fa_coga_dpba", "amort_intang_assets"],
        )
        balancesheet = self.loader.load_dataset(
            "balancesheet",
            symbol=ts_code,
            fields=["end_date", "f_ann_date", "ann_date", "lt_borr", "st_borr", "total_assets", "total_liab", "total_hldr_eqy_exc_min_int"],
        )

        descriptors.update(self._compute_beta_hsigma_from_aligned(aligned_short))
        descriptors["RSTR"] = self._compute_rstr_from_aligned(aligned_long)
        descriptors["LNCAP"] = math.log(max(total_mv, 1e-12))
        descriptors["DASTD"] = self._compute_dastd_from_aligned(aligned_short)
        descriptors["CMRA"] = self._compute_cmra_from_aligned(aligned_short)
        descriptors.update(self.compute_liquidity(ts_code, trade_date))
        descriptors["ETOP"] = self._compute_etop_from_income(income, trade_date, total_mv)
        descriptors["CETOP"] = self._compute_cetop_from_financials(income, cashflow, trade_date, total_mv)
        descriptors["BTOP"] = self._compute_btop_from_balancesheet(balancesheet, trade_date, total_mv)
        descriptors.update(self._compute_leverage_from_balancesheet(balancesheet, trade_date, total_mv))
        descriptors.update(self._compute_growth_from_income(income, trade_date))

        return {
            "ts_code": ts_code,
            "trade_date": trade_date,
            **descriptors,
            "total_mv": total_mv,
            "turnover_rate": turnover_rate,
            "listed_days": listed_days,
            "is_st": int(is_st),
        }

    def compute_beta_hsigma(self, ts_code: str, trade_date: str, market_symbol: str) -> dict[str, float]:
        aligned = self._aligned_return_frame(ts_code, trade_date, market_symbol, lookback=252)
        return self._compute_beta_hsigma_from_aligned(aligned)

    def compute_rstr(self, ts_code: str, trade_date: str, market_symbol: str) -> float:
        aligned = self._aligned_return_frame(ts_code, trade_date, market_symbol, lookback=525)
        return self._compute_rstr_from_aligned(aligned)

    def compute_dastd(self, ts_code: str, trade_date: str, market_symbol: str) -> float:
        aligned = self._aligned_return_frame(ts_code, trade_date, market_symbol, lookback=252)
        return self._compute_dastd_from_aligned(aligned)

    def compute_cmra(self, ts_code: str, trade_date: str, market_symbol: str) -> float:
        aligned = self._aligned_return_frame(ts_code, trade_date, market_symbol, lookback=252)
        return self._compute_cmra_from_aligned(aligned)

    def _compute_beta_hsigma_from_aligned(self, aligned: pd.DataFrame) -> dict[str, float]:
        if len(aligned) < 126:
            return {"BETA": math.nan, "HSIGMA": math.nan}

        stock_excess = aligned["stock_return"].to_numpy(dtype=float) - aligned["risk_free_return"].to_numpy(dtype=float)
        market_excess = aligned["market_return"].to_numpy(dtype=float) - aligned["risk_free_return"].to_numpy(dtype=float)
        beta, residual_std = self._weighted_regression(stock_excess, market_excess, half_life=63)
        return {"BETA": beta, "HSIGMA": residual_std}

    def _compute_rstr_from_aligned(self, aligned: pd.DataFrame) -> float:
        if len(aligned) < 252:
            return math.nan

        lagged = aligned.iloc[:-21] if len(aligned) > 21 else pd.DataFrame()
        if lagged.empty:
            return math.nan

        stock_log = np.log1p(lagged["stock_return"].to_numpy(dtype=float))
        risk_free_log = np.log1p(np.clip(lagged["risk_free_return"].to_numpy(dtype=float), -0.999999, None))
        return self._weighted_mean(stock_log - risk_free_log, half_life=126)

    def _compute_dastd_from_aligned(self, aligned: pd.DataFrame) -> float:
        if len(aligned) < 126:
            return math.nan

        excess = aligned["stock_return"].to_numpy(dtype=float) - aligned["risk_free_return"].to_numpy(dtype=float)
        return self._weighted_std(excess, half_life=42)

    def _compute_cmra_from_aligned(self, aligned: pd.DataFrame) -> float:
        if len(aligned) < 252:
            return math.nan

        excess_log = np.log1p(aligned["stock_return"].to_numpy(dtype=float)) - np.log1p(
            np.clip(aligned["risk_free_return"].to_numpy(dtype=float), -0.999999, None)
        )
        monthly = []
        for month_index in range(12):
            end = len(excess_log) - month_index * 21
            start = max(0, end - 21)
            window = excess_log[start:end]
            if len(window) == 0:
                continue
            monthly.append(float(window.sum()))
        if len(monthly) < 3:
            return math.nan
        cumulative = np.cumsum(monthly[::-1])
        return float(np.max(cumulative) - np.min(cumulative))

    def compute_liquidity(self, ts_code: str, trade_date: str) -> dict[str, float]:
        daily = self.loader.load_dataset("daily", symbol=ts_code, end_date=trade_date, fields=["trade_date", "vol"])
        daily_basic = self.loader.load_point_in_time("daily_basic", symbol=ts_code, asof_date=trade_date, fields=["float_share"])
        if daily.empty or daily_basic is None:
            return {"STOM": math.nan, "STOQ": math.nan, "STOA": math.nan}

        float_share = self._to_float(daily_basic.get("float_share"))
        if float_share is None or float_share <= 0:
            return {"STOM": math.nan, "STOQ": math.nan, "STOA": math.nan}

        volume = self._numeric(daily["vol"])
        return {
            "STOM": self._safe_log(volume.tail(21).sum() / float_share),
            "STOQ": self._safe_log(volume.tail(63).sum() / float_share),
            "STOA": self._safe_log(volume.tail(252).sum() / float_share),
        }

    def compute_etop(self, ts_code: str, trade_date: str, total_mv: float) -> float:
        income = self.loader.load_dataset("income", symbol=ts_code, fields=["end_date", "f_ann_date", "ann_date", "n_income_attr_p"])
        return self._compute_etop_from_income(income, trade_date, total_mv)

    def compute_cetop(self, ts_code: str, trade_date: str, total_mv: float) -> float:
        income = self.loader.load_dataset("income", symbol=ts_code, fields=["end_date", "f_ann_date", "ann_date", "n_income_attr_p"])
        cashflow = self.loader.load_dataset(
            "cashflow",
            symbol=ts_code,
            fields=["end_date", "f_ann_date", "ann_date", "depr_fa_coga_dpba", "amort_intang_assets"],
        )
        return self._compute_cetop_from_financials(income, cashflow, trade_date, total_mv)

    def _compute_etop_from_income(self, income: pd.DataFrame, trade_date: str, total_mv: float) -> float:
        ttm_income = self._compute_ttm(income, "n_income_attr_p", trade_date)
        if ttm_income is None:
            return math.nan
        return ttm_income / total_mv

    def _compute_cetop_from_financials(
        self,
        income: pd.DataFrame,
        cashflow: pd.DataFrame,
        trade_date: str,
        total_mv: float,
    ) -> float:
        ttm_income = self._compute_ttm(income, "n_income_attr_p", trade_date)
        ttm_depr = self._compute_ttm(cashflow, "depr_fa_coga_dpba", trade_date) or 0.0
        ttm_amort = self._compute_ttm(cashflow, "amort_intang_assets", trade_date) or 0.0
        if ttm_income is None:
            return math.nan
        return (ttm_income + ttm_depr + ttm_amort) / total_mv

    def compute_btop(self, ts_code: str, trade_date: str, total_mv: float) -> float:
        balancesheet = self.loader.load_dataset(
            "balancesheet",
            symbol=ts_code,
            fields=["end_date", "f_ann_date", "ann_date", "total_hldr_eqy_exc_min_int"],
        )
        return self._compute_btop_from_balancesheet(balancesheet, trade_date, total_mv)

    def _compute_btop_from_balancesheet(self, balancesheet: pd.DataFrame, trade_date: str, total_mv: float) -> float:
        latest = self._latest_financial_record(
            balancesheet,
            asof_date=trade_date,
        )
        if latest is None:
            return math.nan

        book_equity = self._to_float(latest.get("total_hldr_eqy_exc_min_int"))
        if book_equity is None:
            return math.nan
        return book_equity / total_mv

    def compute_leverage(self, ts_code: str, trade_date: str, total_mv: float) -> dict[str, float]:
        balancesheet = self.loader.load_dataset(
            "balancesheet",
            symbol=ts_code,
            fields=["end_date", "f_ann_date", "ann_date", "lt_borr", "st_borr", "total_assets", "total_liab", "total_hldr_eqy_exc_min_int"],
        )
        return self._compute_leverage_from_balancesheet(balancesheet, trade_date, total_mv)

    def _compute_leverage_from_balancesheet(
        self,
        balancesheet: pd.DataFrame,
        trade_date: str,
        total_mv: float,
    ) -> dict[str, float]:
        latest = self._latest_financial_record(
            balancesheet,
            asof_date=trade_date,
        )
        if latest is None:
            return {"MLEV": math.nan, "DTOA": math.nan, "BLEV": math.nan}

        long_debt = self._to_float(latest.get("lt_borr")) or 0.0
        short_debt = self._to_float(latest.get("st_borr")) or 0.0
        total_assets = self._to_float(latest.get("total_assets"))
        total_liab = self._to_float(latest.get("total_liab"))
        book_equity = self._to_float(latest.get("total_hldr_eqy_exc_min_int"))

        return {
            "MLEV": (total_mv + long_debt + short_debt) / total_mv if total_mv and total_mv > 0 else math.nan,
            "DTOA": total_liab / total_assets if (total_liab is not None and total_assets is not None and total_assets > 0) else math.nan,
            "BLEV": (book_equity + long_debt + short_debt) / book_equity if (book_equity is not None and book_equity > 0) else math.nan,
        }

    def compute_growth(self, ts_code: str, trade_date: str) -> dict[str, float]:
        income = self.loader.load_dataset(
            "income",
            symbol=ts_code,
            fields=["end_date", "f_ann_date", "ann_date", "revenue", "n_income_attr_p"],
        )
        return self._compute_growth_from_income(income, trade_date)

    def _compute_growth_from_income(self, income: pd.DataFrame, trade_date: str) -> dict[str, float]:
        prepared = self._prepare_financial_frame(income, trade_date)
        if prepared.empty:
            return {"SGRO": math.nan, "EGRO": math.nan}

        annual = prepared[prepared["end_date"].astype(str).str.endswith("1231")].sort_values("end_date")
        annual = annual.drop_duplicates(subset=["end_date"], keep="last").tail(5)
        return {
            "SGRO": self._growth_slope(annual.get("revenue")),
            "EGRO": self._growth_slope(annual.get("n_income_attr_p")),
        }

    def _compose_and_standardize(self, frame: pd.DataFrame) -> pd.DataFrame:
        data = frame.copy()
        for descriptor in [column for column in DESCRIPTOR_COLUMNS if column != "NLSIZE"]:
            data[descriptor] = self._winsorize_zscore(data[descriptor])

        raw_lncap = self._numeric(frame["LNCAP"])
        data["NLSIZE"] = self._winsorize_zscore(self._cross_section_residual(raw_lncap.pow(3), raw_lncap))

        for factor_name, composition in FACTOR_COMPOSITION.items():
            data[factor_name] = self._compose_factor(data, composition)
            data[factor_name] = self._winsorize_zscore(data[factor_name])

        data["resvol"] = self._winsorize_zscore(self._cross_section_residual(data["resvol"], data["beta"]))
        return data

    def _compose_factor(self, frame: pd.DataFrame, composition: dict[str, float]) -> pd.Series:
        values = []
        for _, row in frame.iterrows():
            numerator = 0.0
            denominator = 0.0
            for descriptor, weight in composition.items():
                value = self._to_float(row.get(descriptor))
                if value is None or math.isnan(value):
                    continue
                numerator += value * weight
                denominator += abs(weight)
            values.append(numerator / denominator if denominator > 0 else math.nan)
        return pd.Series(values, index=frame.index, dtype=float)

    def _load_market_cap_and_turnover(self, ts_code: str, trade_date: str) -> tuple[float | None, float | None]:
        latest = self.loader.load_point_in_time(
            "daily_basic",
            symbol=ts_code,
            asof_date=trade_date,
            fields=["total_mv", "turnover_rate", "turnover_rate_f"],
        )
        if latest is None:
            return None, None

        total_mv = self._to_float(latest.get("total_mv"))
        if total_mv is None or total_mv <= 0:
            return None, None

        turnover_rate = self._to_float(latest.get("turnover_rate_f"))
        if turnover_rate is None:
            turnover_rate = self._to_float(latest.get("turnover_rate"))
        return total_mv, turnover_rate

    def _load_stock_flags(self, ts_code: str, trade_date: str) -> tuple[int | None, bool]:
        if self._stock_basic_lookup is None:
            frame = self.loader.load_dataset("stock_basic", fields=["ts_code", "name", "list_date"])
            self._stock_basic_lookup = {}
            if not frame.empty and "ts_code" in frame.columns:
                for _, row in frame.iterrows():
                    symbol = str(row.get("ts_code") or "")
                    if not symbol:
                        continue
                    self._stock_basic_lookup[symbol] = (
                        str(row.get("name") or ""),
                        str(row.get("list_date") or ""),
                    )

        if not self._stock_basic_lookup:
            return None, False

        name, list_date = self._stock_basic_lookup.get(ts_code, ("", ""))
        if not name and not list_date:
            return None, False
        listed_days = None
        if len(list_date) == 8 and list_date.isdigit():
            listed_days = (pd.Timestamp(trade_date) - pd.Timestamp(list_date)).days
        is_st = "ST" in name.upper()
        return listed_days, is_st

    def _aligned_return_frame(self, ts_code: str, trade_date: str, market_symbol: str, lookback: int) -> pd.DataFrame:
        daily = self.loader.load_dataset(
            "daily",
            symbol=ts_code,
            end_date=trade_date,
            fields=["trade_date", "pct_chg", "pre_close", "close"],
        )
        market = self.loader.load_dataset(
            "index_daily",
            symbol=market_symbol,
            end_date=trade_date,
            fields=["trade_date", "pct_chg", "pre_close", "close"],
        )
        if daily.empty or market.empty:
            return pd.DataFrame(columns=["trade_date", "stock_return", "market_return", "risk_free_return"])

        daily = daily.tail(max(lookback, 1) + 32).copy()
        market = market.tail(max(lookback, 1) + 32).copy()
        daily["stock_return"] = self._daily_return_series(daily)
        market["market_return"] = self._daily_return_series(market)

        aligned = daily[["trade_date", "stock_return"]].merge(
            market[["trade_date", "market_return"]],
            on="trade_date",
            how="inner",
        )
        risk_free = self._risk_free_frame(start_date=aligned["trade_date"].min(), end_date=trade_date)
        if not risk_free.empty:
            aligned = aligned.merge(risk_free, on="trade_date", how="left")
        if "risk_free_return" not in aligned.columns:
            aligned["risk_free_return"] = 0.0
        aligned["risk_free_return"] = self._numeric(aligned["risk_free_return"]).ffill().fillna(0.0)
        return aligned.tail(lookback).reset_index(drop=True)

    def _risk_free_frame(self, start_date: str, end_date: str) -> pd.DataFrame:
        frame = self.loader.load_dataset("shibor", start_date=start_date, end_date=end_date)
        if frame.empty:
            return pd.DataFrame(columns=["trade_date", "risk_free_return"])

        date_column = "date" if "date" in frame.columns else "trade_date"
        rate_column = None
        for candidate in ["1y", "1Y", "shibor_1y", "rate"]:
            if candidate in frame.columns:
                rate_column = candidate
                break
        if rate_column is None:
            return pd.DataFrame(columns=["trade_date", "risk_free_return"])

        data = frame[[date_column, rate_column]].copy()
        data["trade_date"] = data[date_column].astype(str).str.zfill(8)
        annual_rate = self._numeric(data[rate_column]) / 100.0
        data["risk_free_return"] = annual_rate / 252.0
        return data[["trade_date", "risk_free_return"]].drop_duplicates(subset=["trade_date"], keep="last")

    def _daily_return_series(self, frame: pd.DataFrame) -> pd.Series:
        if "pct_chg" in frame.columns:
            pct_chg = self._numeric(frame["pct_chg"])
            if pct_chg.notna().sum() > 0:
                return pct_chg / 100.0
        close = self._numeric(frame.get("close"))
        pre_close = self._numeric(frame.get("pre_close")).replace(0.0, np.nan)
        if close is None or pre_close is None:
            return pd.Series(np.nan, index=frame.index, dtype=float)
        return close / pre_close - 1.0

    def _prepare_financial_frame(self, frame: pd.DataFrame, asof_date: str) -> pd.DataFrame:
        if frame is None or frame.empty or "end_date" not in frame.columns:
            return pd.DataFrame()

        data = frame.copy()
        for field in ["end_date", "f_ann_date", "ann_date"]:
            if field in data.columns:
                data[field] = data[field].astype(str).str.zfill(8)

        availability = None
        if "f_ann_date" in data.columns:
            availability = data["f_ann_date"]
        elif "ann_date" in data.columns:
            availability = data["ann_date"]
        elif "end_date" in data.columns:
            availability = data["end_date"]

        if availability is not None:
            data = data[availability.astype(str) <= asof_date]

        if data.empty:
            return pd.DataFrame()

        sort_fields = [field for field in ["end_date", "ann_date", "f_ann_date"] if field in data.columns]
        return data.sort_values(sort_fields).reset_index(drop=True)

    def _latest_financial_record(self, frame: pd.DataFrame, asof_date: str) -> pd.Series | None:
        prepared = self._prepare_financial_frame(frame, asof_date)
        if prepared.empty:
            return None
        return prepared.iloc[-1]

    def _compute_ttm(self, frame: pd.DataFrame, field: str, asof_date: str) -> float | None:
        prepared = self._prepare_financial_frame(frame, asof_date)
        if prepared.empty or field not in prepared.columns:
            return None

        prepared = prepared.dropna(subset=[field])
        if prepared.empty:
            return None

        prepared = prepared.drop_duplicates(subset=["end_date"], keep="last").sort_values("end_date")
        latest = prepared.iloc[-1]
        latest_value = self._to_float(latest.get(field))
        if latest_value is None:
            return None

        latest_end = str(latest["end_date"])
        if latest_end.endswith("1231"):
            return latest_value

        latest_year = int(latest_end[:4])
        quarter = latest_end[4:]
        annual = prepared[prepared["end_date"].astype(str) == f"{latest_year - 1}1231"]
        prev_quarter = prepared[prepared["end_date"].astype(str) == f"{latest_year - 1}{quarter}"]
        if annual.empty or prev_quarter.empty:
            return latest_value

        annual_value = self._to_float(annual.iloc[-1].get(field))
        prev_quarter_value = self._to_float(prev_quarter.iloc[-1].get(field))
        if annual_value is None or prev_quarter_value is None:
            return latest_value
        return latest_value + annual_value - prev_quarter_value

    def _growth_slope(self, series: pd.Series | None) -> float:
        if series is None:
            return math.nan
        numeric = self._numeric(series).dropna()
        if len(numeric) < 3:
            return math.nan

        transformed = np.sign(numeric.to_numpy(dtype=float)) * np.log1p(np.abs(numeric.to_numpy(dtype=float)))
        x = np.arange(len(transformed), dtype=float)
        slope = np.polyfit(x, transformed, 1)[0]
        return float(slope)

    @staticmethod
    def _winsorize_zscore(series: pd.Series) -> pd.Series:
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().sum() <= 1:
            return pd.Series(np.nan, index=series.index, dtype=float)

        mean = numeric.mean()
        std = numeric.std(ddof=0)
        if pd.isna(std) or math.isclose(float(std), 0.0):
            return pd.Series(0.0, index=series.index, dtype=float)

        clipped = numeric.clip(lower=mean - 3 * std, upper=mean + 3 * std)
        clipped_std = clipped.std(ddof=0)
        if pd.isna(clipped_std) or math.isclose(float(clipped_std), 0.0):
            return pd.Series(0.0, index=series.index, dtype=float)
        return (clipped - clipped.mean()) / clipped_std

    @staticmethod
    def _cross_section_residual(y: pd.Series, x: pd.Series) -> pd.Series:
        y_numeric = pd.to_numeric(y, errors="coerce")
        x_numeric = pd.to_numeric(x, errors="coerce")
        valid = y_numeric.notna() & x_numeric.notna()
        residual = pd.Series(np.nan, index=y.index, dtype=float)
        if valid.sum() <= 1:
            return residual

        y_values = y_numeric[valid].to_numpy(dtype=float)
        x_values = x_numeric[valid].to_numpy(dtype=float)
        x_centered = x_values - x_values.mean()
        y_centered = y_values - y_values.mean()
        denominator = float(np.dot(x_centered, x_centered))
        if math.isclose(denominator, 0.0):
            residual.loc[valid] = y_centered
            return residual

        beta = float(np.dot(x_centered, y_centered) / denominator)
        residual.loc[valid] = y_centered - beta * x_centered
        return residual

    @staticmethod
    def _weighted_regression(y: np.ndarray, x: np.ndarray, half_life: int) -> tuple[float, float]:
        valid = np.isfinite(y) & np.isfinite(x)
        if valid.sum() <= 1:
            return math.nan, math.nan

        y = y[valid]
        x = x[valid]
        weights = BarraCNE5FactorBuilder._exponential_weights(len(y), half_life)
        x_mean = np.average(x, weights=weights)
        y_mean = np.average(y, weights=weights)
        x_centered = x - x_mean
        y_centered = y - y_mean
        denominator = float(np.sum(weights * x_centered * x_centered))
        if math.isclose(denominator, 0.0):
            return math.nan, math.nan

        beta = float(np.sum(weights * x_centered * y_centered) / denominator)
        residuals = y_centered - beta * x_centered
        residual_std = math.sqrt(float(np.sum(weights * residuals * residuals) / np.sum(weights)))
        return beta, residual_std

    @staticmethod
    def _weighted_mean(values: np.ndarray, half_life: int) -> float:
        valid = np.isfinite(values)
        if valid.sum() == 0:
            return math.nan
        sample = values[valid]
        weights = BarraCNE5FactorBuilder._exponential_weights(len(sample), half_life)
        return float(np.average(sample, weights=weights))

    @staticmethod
    def _weighted_std(values: np.ndarray, half_life: int) -> float:
        valid = np.isfinite(values)
        if valid.sum() <= 1:
            return math.nan
        sample = values[valid]
        weights = BarraCNE5FactorBuilder._exponential_weights(len(sample), half_life)
        mean = float(np.average(sample, weights=weights))
        variance = float(np.average((sample - mean) ** 2, weights=weights))
        return math.sqrt(max(variance, 0.0))

    @staticmethod
    def _exponential_weights(length: int, half_life: int) -> np.ndarray:
        if length <= 0:
            return np.array([], dtype=float)
        decay = math.log(2.0) / max(int(half_life), 1)
        age = np.arange(length - 1, -1, -1, dtype=float)
        weights = np.exp(-decay * age)
        return weights / weights.sum()

    @staticmethod
    def _safe_log(value: float | int | None) -> float:
        if value is None or not math.isfinite(float(value)) or float(value) <= 0:
            return math.nan
        return float(math.log(float(value)))

    @staticmethod
    def _numeric(series: pd.Series | None) -> pd.Series:
        if series is None:
            return pd.Series(dtype=float)
        return pd.to_numeric(series, errors="coerce")

    @staticmethod
    def _to_float(value) -> float | None:
        if value is None:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None
