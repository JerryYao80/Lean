#!/usr/bin/env python
"""
适合由 supervisord 托管的常驻增量调度器。

行为：
1. 常驻运行；
2. 每隔一段时间轮询当前时间；
3. 仅在上海时区交易日 16:00 之后触发增量更新；
4. 同一目标交易日只会成功执行一次；
5. 若某次执行失败，则按重试间隔继续重试。
"""
import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from incremental_update import IncrementalUpdater, SHANGHAI_TZ, setup_logging


LOGGER = logging.getLogger("incremental_scheduler")
SCHEDULER_STATE_FILE = Path("download_state/incremental_scheduler_state.json")
CORE_SCHEDULED_APIS = [
    "trade_cal",
    "daily",
    "adj_factor",
    "daily_basic",
    "moneyflow",
    "suspend_d",
    "stk_limit",
]


def parse_list_argument(value: Optional[str]) -> Optional[Sequence[str]]:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


class SchedulerState:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.state = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.file_path.exists():
            return {}
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("Failed to load scheduler state %s: %s", self.file_path, exc)
            return {}

    def save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    def update(self, **kwargs: Any) -> None:
        self.state.update(kwargs)
        self.state["updated_at"] = datetime.now(SHANGHAI_TZ).isoformat()
        self.save()


class IncrementalScheduler:
    def __init__(
        self,
        api_names: Optional[Sequence[str]] = None,
        categories: Optional[Sequence[str]] = None,
        bootstrap_trade_days: int = 3,
        lookback_trade_days: int = 2,
        cutoff_hour: int = 16,
        poll_seconds: int = 60,
        retry_interval_minutes: int = 30,
    ):
        self.api_names = list(api_names) if api_names else None
        self.categories = categories
        if self.api_names is None and self.categories is None:
            self.api_names = CORE_SCHEDULED_APIS.copy()
        self.bootstrap_trade_days = bootstrap_trade_days
        self.lookback_trade_days = lookback_trade_days
        self.cutoff_hour = cutoff_hour
        self.poll_seconds = poll_seconds
        self.retry_interval_minutes = retry_interval_minutes
        self.state = SchedulerState(SCHEDULER_STATE_FILE)

    def _build_updater(self) -> IncrementalUpdater:
        return IncrementalUpdater(
            api_names=self.api_names,
            categories=self.categories,
            bootstrap_trade_days=self.bootstrap_trade_days,
            lookback_trade_days=self.lookback_trade_days,
            cutoff_hour=self.cutoff_hour,
            dry_run=False,
        )

    def _should_wait_for_retry(self, target_date: str, now: datetime) -> bool:
        last_attempt_target = self.state.get("last_attempt_target_date")
        last_attempt_at = self.state.get("last_attempt_at")
        last_status = self.state.get("last_status")

        if last_attempt_target != target_date or last_status != "crashed" or not last_attempt_at:
            return False

        last_attempt_time = datetime.fromisoformat(last_attempt_at)
        elapsed_seconds = (now - last_attempt_time).total_seconds()
        return elapsed_seconds < self.retry_interval_minutes * 60

    def tick(self) -> bool:
        now = datetime.now(SHANGHAI_TZ)
        if now.hour < self.cutoff_hour:
            LOGGER.debug("Before cutoff hour %s, waiting", self.cutoff_hour)
            return False

        updater = self._build_updater()
        target_date = updater._resolve_target_trade_date()
        last_finished_date = self.state.get("last_finished_target_date")

        if last_finished_date == target_date:
            LOGGER.info("Target trade date %s already finished today, skip", target_date)
            return False

        if self._should_wait_for_retry(target_date, now):
            LOGGER.info("Previous attempt for %s failed recently, waiting before retry", target_date)
            return False

        LOGGER.info("Starting incremental run for target trade date %s", target_date)
        self.state.update(
            last_attempt_target_date=target_date,
            last_attempt_at=now.isoformat(),
            last_status="running",
        )

        try:
            summary = updater.run(forced_date=target_date)
            status = "success" if summary.get("failed", 0) == 0 else "partial_failed"
            state_payload = {
                "last_finished_target_date": target_date,
                "last_summary": summary,
                "last_status": status,
            }
            if status == "success":
                state_payload["last_success_target_date"] = target_date
            self.state.update(**state_payload)

            if status == "success":
                LOGGER.info("Incremental run completed successfully for %s", target_date)
                return True

            LOGGER.warning("Incremental run finished with partial failures for %s", target_date)
            return True
        except Exception as exc:
            LOGGER.exception("Incremental run crashed for %s", target_date)
            self.state.update(last_status="crashed", last_error=str(exc))
            return False

    def run_forever(self) -> None:
        LOGGER.info(
            "Scheduler started: cutoff=%s:00 poll=%ss retry=%smin",
            self.cutoff_hour,
            self.poll_seconds,
            self.retry_interval_minutes,
        )
        while True:
            self.tick()
            time.sleep(self.poll_seconds)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervisor 常驻增量调度器")
    parser.add_argument("--api", type=str, help="指定接口，逗号分隔")
    parser.add_argument("--category", type=str, help="指定类别，逗号分隔")
    parser.add_argument("--bootstrap-trade-days", type=int, default=3, help="首次回补交易日数量")
    parser.add_argument("--lookback-trade-days", type=int, default=2, help="每次回刷最近交易日数量")
    parser.add_argument("--cutoff-hour", type=int, default=16, help="默认 16 点后触发")
    parser.add_argument("--poll-seconds", type=int, default=60, help="轮询间隔秒数")
    parser.add_argument("--retry-interval-minutes", type=int, default=30, help="失败后的最小重试间隔")
    return parser



def main() -> None:
    setup_logging()
    args = build_parser().parse_args()

    scheduler = IncrementalScheduler(
        api_names=parse_list_argument(args.api),
        categories=parse_list_argument(args.category),
        bootstrap_trade_days=args.bootstrap_trade_days,
        lookback_trade_days=args.lookback_trade_days,
        cutoff_hour=args.cutoff_hour,
        poll_seconds=args.poll_seconds,
        retry_interval_minutes=args.retry_interval_minutes,
    )
    scheduler.run_forever()


if __name__ == "__main__":
    main()
