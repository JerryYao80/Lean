#!/usr/bin/env python
"""
交易日增量更新脚本。

默认行为：
1. 仅更新适合做日增量的接口；
2. 使用上交所 `trade_cal` 判断当日是否为交易日；
3. 在 16:00 之后更新到当日，否则更新到上一个交易日；
4. 首次运行默认回补最近 40 个交易日，后续基于状态文件继续增量。
"""
import argparse
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

import config
from api_registry import APIConfig, ChunkStrategy, get_enabled_apis
from downloader import TushareDownloader


LOGGER = logging.getLogger("incremental_update")
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
STATE_FILE = Path("download_state/incremental_state.json")

DEFAULT_INCREMENTAL_CATEGORIES = [
    "stock_basic",
    "stock_quote",
    "stock_moneyflow",
    "stock_billboard",
    "stock_special",
    "stock_concept",
    "index",
    "etf",
    "futures",
    "options",
    "cb",
    "fx",
    "hk",
    "spot",
]

SNAPSHOT_APIS = {"trade_cal", "hk_tradecal"}
TRADE_DATE_BATCH_APIS = {
    "daily",
    "adj_factor",
    "daily_basic",
    "stk_limit",
    "moneyflow",
    "fund_daily",
}
PER_CODE_RANGE_APIS = {"index_daily"}
DATE_FIELD_OVERRIDES = {
    "fund_daily": "trade_date",
    "index_daily": "trade_date",
    "moneyflow": "trade_date",
}


def setup_logging() -> None:
    Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format=config.LOG_FORMAT,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                Path(config.LOG_DIR) / "incremental_update.log",
                encoding="utf-8",
            ),
        ],
    )


def parse_yyyymmdd(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d")


def format_yyyymmdd(value: datetime) -> str:
    return value.strftime("%Y%m%d")


def dedupe_api_configs(api_configs: Sequence[APIConfig]) -> List[APIConfig]:
    seen = set()
    deduped: List[APIConfig] = []
    for api_config in api_configs:
        if api_config.api_name in seen:
            continue
        seen.add(api_config.api_name)
        deduped.append(api_config)
    return deduped


def merge_frames(existing_df: pd.DataFrame, new_df: pd.DataFrame, api_config: APIConfig) -> pd.DataFrame:
    frames = [df for df in (existing_df, new_df) if df is not None and not df.empty]
    if not frames:
        return pd.DataFrame()
    if len(frames) == 1:
        combined = frames[0].copy()
    else:
        combined = pd.concat(frames, ignore_index=True)

    dedupe_columns: List[str] = []
    candidate_columns = [
        api_config.code_field,
        "ts_code",
        api_config.date_field,
        DATE_FIELD_OVERRIDES.get(api_config.api_name),
        "trade_date",
        "cal_date",
        "period",
        "ann_date",
        "end_date",
        "date",
        "exchange",
    ]

    for column in candidate_columns:
        if column and column in combined.columns and column not in dedupe_columns:
            dedupe_columns.append(column)

    if dedupe_columns:
        combined = combined.drop_duplicates(subset=dedupe_columns, keep="last")
        combined = combined.sort_values(dedupe_columns).reset_index(drop=True)
        return combined

    return combined.drop_duplicates().reset_index(drop=True)


def choose_start_date(
    open_dates: Sequence[str],
    latest_completed_date: Optional[str],
    target_date: str,
    bootstrap_trade_days: int,
    lookback_trade_days: int,
) -> str:
    target_index = open_dates.index(target_date)
    bootstrap_index = max(0, target_index - bootstrap_trade_days + 1)
    lookback_index = max(0, target_index - lookback_trade_days + 1)

    if latest_completed_date and latest_completed_date in open_dates:
        next_index = open_dates.index(latest_completed_date) + 1
        if next_index <= target_index:
            return min(open_dates[next_index], open_dates[lookback_index])
        return open_dates[lookback_index]

    return open_dates[bootstrap_index]


class SkipIncrementalAPI(Exception):
    """用于表示该接口应被跳过，而不是记为失败。"""


class IncrementalState:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.state = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.file_path.exists():
            return {"apis": {}}
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("Failed to load state file %s: %s", self.file_path, exc)
            return {"apis": {}}

    def get_api_state(self, api_name: str) -> Dict[str, Any]:
        return self.state.setdefault("apis", {}).setdefault(api_name, {})

    def update_api_state(self, api_name: str, **kwargs: Any) -> None:
        api_state = self.get_api_state(api_name)
        api_state.update(kwargs)
        api_state["updated_at"] = datetime.now(SHANGHAI_TZ).isoformat()

    def save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class IncrementalUpdater:
    def __init__(
        self,
        api_names: Optional[Sequence[str]] = None,
        categories: Optional[Sequence[str]] = None,
        bootstrap_trade_days: int = 40,
        lookback_trade_days: int = 3,
        cutoff_hour: int = 16,
        dry_run: bool = False,
    ):
        self.downloader = TushareDownloader()
        self.state = IncrementalState(STATE_FILE)
        self.api_names = set(api_names or [])
        self.categories = list(categories or DEFAULT_INCREMENTAL_CATEGORIES)
        self.bootstrap_trade_days = bootstrap_trade_days
        self.lookback_trade_days = lookback_trade_days
        self.cutoff_hour = cutoff_hour
        self.dry_run = dry_run
        self._trade_dates_cache: Dict[tuple[str, str], List[str]] = {}

    def _select_api_configs(self) -> List[APIConfig]:
        api_configs = dedupe_api_configs(get_enabled_apis())

        if self.api_names:
            api_configs = [api for api in api_configs if api.api_name in self.api_names]
        else:
            api_configs = [api for api in api_configs if api.category in self.categories]

        selected = []
        for api_config in api_configs:
            if self._supports_incremental(api_config):
                selected.append(api_config)
            else:
                LOGGER.info("Skipping unsupported incremental API: %s", api_config.api_name)

        return selected

    def _supports_incremental(self, api_config: APIConfig) -> bool:
        if api_config.api_name in SNAPSHOT_APIS:
            return True
        if api_config.api_name in TRADE_DATE_BATCH_APIS:
            return True
        if api_config.api_name in PER_CODE_RANGE_APIS:
            return True
        if api_config.chunk_strategy in {ChunkStrategy.DATE, ChunkStrategy.YEAR} and api_config.date_field:
            return True
        return False

    def _call_api(self, api_config: APIConfig, **kwargs: Any) -> Optional[pd.DataFrame]:
        params = api_config.required_params.copy()
        params.update(kwargs)
        return self.downloader._call_api_with_retry(api_config.api_name, **params)

    def _read_parquet(self, file_path: Path) -> pd.DataFrame:
        if not file_path.exists():
            return pd.DataFrame()
        try:
            return pd.read_parquet(file_path, engine="pyarrow")
        except Exception as exc:
            LOGGER.warning("Failed to read parquet %s: %s", file_path, exc)
            return pd.DataFrame()

    def _write_parquet(self, df: pd.DataFrame, file_path: Path) -> bool:
        if self.dry_run:
            LOGGER.info("[dry-run] Would write %s rows to %s", len(df), file_path)
            return True
        return self.downloader._save_to_parquet(df, file_path)

    def _get_open_dates(self, start_date: str, end_date: str) -> List[str]:
        cache_key = (start_date, end_date)
        if cache_key in self._trade_dates_cache:
            return self._trade_dates_cache[cache_key]

        df = self.downloader._call_api_with_retry(
            "trade_cal",
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
        )
        if df is None or df.empty:
            LOGGER.warning("Falling back to local trade_cal parquet for %s - %s", start_date, end_date)
            local_file = self.downloader._get_file_path("trade_cal")
            df = self._read_parquet(local_file)
            if df.empty:
                raise RuntimeError("Failed to fetch SSE trade calendar")

        trade_df = df.copy()
        trade_df["cal_date"] = trade_df["cal_date"].astype(str)
        trade_df["is_open"] = trade_df["is_open"].astype(str)
        trade_df = trade_df[(trade_df["cal_date"] >= start_date) & (trade_df["cal_date"] <= end_date)]
        open_dates = sorted(trade_df.loc[trade_df["is_open"] == "1", "cal_date"].tolist())
        self._trade_dates_cache[cache_key] = open_dates
        return open_dates

    def _resolve_target_trade_date(self, forced_date: Optional[str] = None) -> str:
        now = datetime.now(SHANGHAI_TZ)
        if forced_date:
            lookback_start = format_yyyymmdd(now - timedelta(days=60))
            open_dates = self._get_open_dates(lookback_start, forced_date)
            if forced_date not in open_dates:
                raise ValueError(f"{forced_date} 不是上交所交易日")
            return forced_date

        today = format_yyyymmdd(now)
        open_dates = self._get_open_dates(format_yyyymmdd(now - timedelta(days=60)), today)
        if not open_dates:
            raise RuntimeError("Failed to resolve latest trade date")

        if today in open_dates and now.hour >= self.cutoff_hour:
            return today

        for trade_date in reversed(open_dates):
            if trade_date < today:
                return trade_date

        raise RuntimeError("No historical trade date available before today")

    def _get_trade_dates_window(self, target_date: str, latest_completed_date: Optional[str]) -> List[str]:
        start_date = format_yyyymmdd(parse_yyyymmdd(target_date) - timedelta(days=120))
        open_dates = self._get_open_dates(start_date, target_date)
        if target_date not in open_dates:
            raise RuntimeError(f"Target date {target_date} is not an open trade date")

        first_date = choose_start_date(
            open_dates=open_dates,
            latest_completed_date=latest_completed_date,
            target_date=target_date,
            bootstrap_trade_days=self.bootstrap_trade_days,
            lookback_trade_days=self.lookback_trade_days,
        )
        return [trade_date for trade_date in open_dates if first_date <= trade_date <= target_date]

    def _date_param_name(self, api_config: APIConfig) -> str:
        return DATE_FIELD_OVERRIDES.get(api_config.api_name, api_config.date_field or "trade_date")

    def _raise_for_failed_call(self, api_config: APIConfig, scope: str) -> None:
        call_result = self.downloader.get_last_call_result()
        error_type = call_result.get("type")
        message = call_result.get("message", "")

        if error_type in {"permission_denied", "api_not_found"}:
            raise SkipIncrementalAPI(f"{api_config.api_name} skipped ({scope}): {message}")

        raise RuntimeError(f"API call failed for {api_config.api_name} {scope}")

    def _refresh_snapshot_api(self, api_config: APIConfig, target_date: str) -> Dict[str, Any]:
        if api_config.api_name == "trade_cal":
            start_date = f"{parse_yyyymmdd(target_date).year - 1}0101"
            end_date = f"{parse_yyyymmdd(target_date).year + 1}1231"
            df = self.downloader._call_api_with_retry(
                "trade_cal",
                exchange="SSE",
                start_date=start_date,
                end_date=end_date,
            )
        else:
            df = self._call_api(api_config)

        if df is None:
            self._raise_for_failed_call(api_config, "snapshot")

        file_path = self.downloader._get_file_path(api_config.api_name)
        self._write_parquet(df, file_path)
        return {"rows": len(df), "files": 1}

    def _refresh_year_partition_api(self, api_config: APIConfig, trade_dates: Sequence[str]) -> Dict[str, Any]:
        affected_years = sorted({int(trade_date[:4]) for trade_date in trade_dates})
        total_rows = 0
        total_files = 0

        for year in affected_years:
            year_dates = [trade_date for trade_date in trade_dates if trade_date.startswith(str(year))]
            start_date = min(year_dates)
            end_date = max(year_dates)

            df_new = self._call_api(
                api_config,
                **{
                    api_config.start_date_param: start_date,
                    api_config.end_date_param: end_date,
                },
            )
            if df_new is None:
                self._raise_for_failed_call(api_config, f"year={year}")

            file_path = self.downloader._get_file_path(api_config.api_name, year=year)
            existing_df = self._read_parquet(file_path)
            merged_df = merge_frames(existing_df, df_new, api_config)
            self._write_parquet(merged_df, file_path)
            total_rows += len(df_new)
            total_files += 1

        return {"rows": total_rows, "files": total_files}

    def _refresh_date_partition_api(self, api_config: APIConfig, trade_dates: Sequence[str]) -> Dict[str, Any]:
        total_rows = 0
        total_files = 0
        date_param = self._date_param_name(api_config)

        for trade_date in trade_dates:
            df_new = self._call_api(api_config, **{date_param: trade_date})
            if df_new is None:
                self._raise_for_failed_call(api_config, f"date={trade_date}")

            file_path = self.downloader._get_file_path(api_config.api_name, date=trade_date)
            self._write_parquet(df_new, file_path)
            total_rows += len(df_new)
            total_files += 1

        return {"rows": total_rows, "files": total_files}

    def _refresh_trade_date_batch_api(self, api_config: APIConfig, trade_dates: Sequence[str]) -> Dict[str, Any]:
        date_param = self._date_param_name(api_config)
        code_field = api_config.code_field or "ts_code"
        frames: List[pd.DataFrame] = []
        api_rows = 0

        for trade_date in trade_dates:
            df_new = self._call_api(api_config, **{date_param: trade_date})
            if df_new is None:
                self._raise_for_failed_call(api_config, f"date={trade_date}")
            if not df_new.empty:
                frames.append(df_new)
                api_rows += len(df_new)

        if not frames:
            LOGGER.warning("No rows returned for %s in %s", api_config.api_name, list(trade_dates))
            return {"rows": 0, "files": 0}

        combined_df = pd.concat(frames, ignore_index=True)
        if code_field not in combined_df.columns:
            raise RuntimeError(f"{api_config.api_name} returned no {code_field} column")

        total_files = 0
        for code_value, group_df in combined_df.groupby(code_field):
            file_path = self.downloader._get_file_path(api_config.api_name, ts_code=str(code_value))
            existing_df = self._read_parquet(file_path)
            merged_df = merge_frames(existing_df, group_df, api_config)
            self._write_parquet(merged_df, file_path)
            total_files += 1

        return {"rows": api_rows, "files": total_files}

    def _refresh_per_code_range_api(self, api_config: APIConfig, trade_dates: Sequence[str]) -> Dict[str, Any]:
        code_field = api_config.code_field or "ts_code"
        ts_codes = self.downloader._get_stock_list(api_config.category)
        if not ts_codes:
            raise RuntimeError(f"No ts_code list available for {api_config.api_name}")

        start_date = min(trade_dates)
        end_date = max(trade_dates)
        total_rows = 0
        total_files = 0

        for ts_code in ts_codes:
            df_new = self._call_api(
                api_config,
                **{
                    code_field: ts_code,
                    api_config.start_date_param: start_date,
                    api_config.end_date_param: end_date,
                },
            )
            if df_new is None:
                LOGGER.warning("Skip %s[%s]: API call failed", api_config.api_name, ts_code)
                continue

            file_path = self.downloader._get_file_path(api_config.api_name, ts_code=ts_code)
            existing_df = self._read_parquet(file_path)
            merged_df = merge_frames(existing_df, df_new, api_config)
            self._write_parquet(merged_df, file_path)
            total_rows += len(df_new)
            total_files += 1

        return {"rows": total_rows, "files": total_files}

    def _run_single_api(self, api_config: APIConfig, target_date: str) -> Dict[str, Any]:
        api_state = self.state.get_api_state(api_config.api_name)
        latest_completed_date = api_state.get("last_target_date")

        if api_config.api_name in SNAPSHOT_APIS:
            result = self._refresh_snapshot_api(api_config, target_date)
        else:
            trade_dates = self._get_trade_dates_window(target_date, latest_completed_date)
            if api_config.api_name in TRADE_DATE_BATCH_APIS:
                result = self._refresh_trade_date_batch_api(api_config, trade_dates)
            elif api_config.api_name in PER_CODE_RANGE_APIS:
                result = self._refresh_per_code_range_api(api_config, trade_dates)
            elif api_config.chunk_strategy == ChunkStrategy.DATE:
                result = self._refresh_date_partition_api(api_config, trade_dates)
            elif api_config.chunk_strategy == ChunkStrategy.YEAR:
                result = self._refresh_year_partition_api(api_config, trade_dates)
            else:
                raise RuntimeError(f"Unsupported incremental strategy for {api_config.api_name}")

        if not self.dry_run:
            self.state.update_api_state(api_config.api_name, last_target_date=target_date)
            self.state.save()
        result["target_date"] = target_date
        return result

    def run(self, forced_date: Optional[str] = None) -> Dict[str, Any]:
        target_date = self._resolve_target_trade_date(forced_date)
        api_configs = self._select_api_configs()
        LOGGER.info("Incremental update target trade date: %s", target_date)
        LOGGER.info("Selected %s incremental APIs", len(api_configs))

        summary = {
            "target_date": target_date,
            "selected": len(api_configs),
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "rows": 0,
            "files": 0,
            "details": {},
        }

        for index, api_config in enumerate(api_configs, start=1):
            LOGGER.info("[%s/%s] Updating %s", index, len(api_configs), api_config.api_name)
            start_ts = time.time()
            try:
                result = self._run_single_api(api_config, target_date)
                result["elapsed_seconds"] = round(time.time() - start_ts, 2)
                summary["details"][api_config.api_name] = {"status": "success", **result}
                summary["completed"] += 1
                summary["rows"] += result["rows"]
                summary["files"] += result["files"]
            except SkipIncrementalAPI as exc:
                LOGGER.warning(str(exc))
                summary.setdefault("skipped", 0)
                summary["details"][api_config.api_name] = {
                    "status": "skipped",
                    "reason": str(exc),
                    "elapsed_seconds": round(time.time() - start_ts, 2),
                }
                if not self.dry_run:
                    self.state.update_api_state(api_config.api_name, last_target_date=target_date, last_status="skipped")
                    self.state.save()
                summary["skipped"] += 1
            except Exception as exc:
                LOGGER.error("Incremental update failed for %s: %s", api_config.api_name, exc)
                summary["details"][api_config.api_name] = {
                    "status": "failed",
                    "error": str(exc),
                    "elapsed_seconds": round(time.time() - start_ts, 2),
                }
                summary["failed"] += 1

        if not self.dry_run:
            self.state.save()

        return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tushare 交易日增量更新")
    parser.add_argument("--api", type=str, help="指定接口，逗号分隔")
    parser.add_argument("--category", type=str, help="指定类别，逗号分隔")
    parser.add_argument("--target-date", type=str, help="强制更新到指定交易日，格式 YYYYMMDD")
    parser.add_argument("--bootstrap-trade-days", type=int, default=40, help="首次运行回补的交易日数量")
    parser.add_argument("--lookback-trade-days", type=int, default=3, help="每次回刷最近几个交易日")
    parser.add_argument("--cutoff-hour", type=int, default=16, help="默认 16 点后才更新当日")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    return parser


def main() -> None:
    setup_logging()
    args = build_parser().parse_args()

    api_names = args.api.split(",") if args.api else None
    categories = args.category.split(",") if args.category else None

    updater = IncrementalUpdater(
        api_names=api_names,
        categories=categories,
        bootstrap_trade_days=args.bootstrap_trade_days,
        lookback_trade_days=args.lookback_trade_days,
        cutoff_hour=args.cutoff_hour,
        dry_run=args.dry_run,
    )

    summary = updater.run(forced_date=args.target_date)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
