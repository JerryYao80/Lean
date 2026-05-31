#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import soloquant_orchestrator as orchestrator


def health_check_searxng(url: str = "http://localhost:11236", timeout: int = 5) -> bool:
    """Ping SearxNG to verify it's reachable before crawling."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/healthz", method="GET")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def health_check_crawl4ai(url: str = "http://localhost:11235", timeout: int = 5) -> bool:
    """Ping crawl4ai to verify it's reachable before crawling."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/health", method="GET")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


STRATEGY_KEYWORDS = [
    "arXiv q-fin alpha factor implementation backtest",
    "SSRN equity anomaly trading strategy backtest",
    "Quantpedia factor strategy implementation",
    "WorldQuant BRAIN alpha expression",
    "QuantConnect Community A-share strategy",
    "Numerai feature neutralization model",
    "华泰 金工 多因子 研报 策略 回测",
    "中信 金工 量化 策略 回测",
    "A股 资金流 多因子 策略",
    "A股 行业轮动 量化 策略",
    "implied volatility trading signal equity ETF backtest",
    "VIX contango backwardation trading strategy arXiv",
    "volatility skew put call signal alpha strategy",
    "implied realized volatility spread trading strategy",
    "A股 隐含波动率 期权 交易策略 回测",
    "波动率偏斜 交易信号 量化策略",
    "50ETF期权 隐含波动率 策略",
]
FINANCE_INTELLIGENCE_KEYWORDS = [
    "site:pbc.gov.cn 公开市场业务交易公告 逆回购 MLF 流动性 利率 2026",
    "site:pbc.gov.cn 货币政策执行报告 LPR 降准 降息 流动性 风险偏好 2026",
    "site:csrc.gov.cn 新闻发布会 监管政策 市场风险 退市 融资融券 2026",
    "site:sse.com.cn/disclosure/notice/general/c/ 交易风险提示 债券 股票 退市 异常交易 2026",
    "site:szse.cn/disclosure/notice/ 交易风险提示 监管动态 异常交易 退市 2026",
    "site:bse.cn 公告 监管动态 市场风险 退市 风险提示 2026",
    "site:paper.cnstock.com 交易风险提示 退市风险 流动性 利率 风险偏好 2026",
    "site:stcn.com/article/detail 央行 交易所 监管 风险偏好 流动性 退市 2026",
    "site:reuters.com China markets liquidity rates risk appetite policy 2026",
    "site:jpmorgan.com OR site:morganstanley.com OR site:goldmansachs.com China equity strategy rates liquidity risk appetite 2026",
    "site:cicc.com OR site:htsc.com.cn OR site:citics.com 宏观策略 A股 流动性 利率 风险偏好 2026",
    "site:federalreserve.gov FOMC federal funds Treasury yield liquidity risk appetite 2026",
    "site:sec.gov OR site:cftc.gov market risk derivatives clearing volatility margin 2026",
    "site:ecb.europa.eu monetary policy rates inflation liquidity market risk 2026",
    "site:bankofengland.co.uk monetary policy gilt yield liquidity inflation 2026",
    "site:boj.or.jp monetary policy yield curve JGB liquidity yen 2026",
    "site:fsa.go.jp OR site:jpx.co.jp market risk derivatives trading halt margin 2026",
    "site:cmegroup.com futures margin clearing volatility interest rates equity index 2026",
    "site:theice.com OR site:cboe.com OR site:eurex.com futures options margin volatility circular notice 2026",
    "site:euronext.com market notice derivatives clearing volatility 2026",
]


def default_state_path(config: dict) -> Path:
    return Path(config["workflow-root"]) / "crawl-scheduler-state.json"


def default_log_path(config: dict) -> Path:
    return Path(config["workflow-root"]) / "soloquant-crawl-scheduler.log"


class SchedulerLogger:
    def __init__(self, log_path: str | Path | None = None):
        self.log_path = Path(log_path) if log_path else None

    def info(self, message: str, **fields) -> None:
        self._write("INFO", message, fields)

    def error(self, message: str, **fields) -> None:
        self._write("ERROR", message, fields)

    def _write(self, level: str, message: str, fields: dict) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        suffix = " ".join(f"{key}={self._format(value)}" for key, value in fields.items() if value is not None)
        line = f"{timestamp} level={level} event={message}"
        if suffix:
            line = f"{line} {suffix}"
        print(line, flush=True)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")

    @staticmethod
    def _format(value) -> str:
        text = str(value)
        if any(ch.isspace() for ch in text):
            return json.dumps(text, ensure_ascii=False)
        return text


def default_crawl_tasks() -> list[dict]:
    return [
        {
            "name": "strategy",
            "category": "strategy",
            "keywords": STRATEGY_KEYWORDS,
            "interval-minutes": 5,
            "max-queries": 80,
            "max-results-per-query": 3,
            "screen-with-llm": True,
        },
        {
            "name": "finance_intelligence",
            "category": "finance_intelligence",
            "keywords": FINANCE_INTELLIGENCE_KEYWORDS,
            "interval-minutes": 5,
            "max-queries": 60,
            "max-results-per-query": 3,
            "screen-with-llm": True,
        },
        {
            "name": "data_driven_strategy",
            "category": "strategy",
            "interval-minutes": 10,
            "max-queries": 40,
            "max-results-per-query": 3,
            "mode": "data_driven",
            "screen-with-llm": True,
        },
    ]


def load_crawl_tasks(config: dict) -> list[dict]:
    configured = config.get("crawl-tasks")
    if isinstance(configured, list) and configured:
        tasks = []
        for item in configured:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("category") or "").strip()
            category = orchestrator.normalize_category(item.get("category") or name)
            keywords = [str(value) for value in (item.get("keywords") or []) if str(value).strip()]
            if not name or not keywords and not item.get("mode"):
                continue
            tasks.append(
                {
                    "name": name,
                    "category": category,
                    "keywords": keywords,
                    "interval-minutes": max(1, int(item.get("interval-minutes") or 60)),
                    "max-queries": max(1, int(item.get("max-queries") or 20)),
                    "max-results-per-query": max(1, int(item.get("max-results-per-query") or 3)),
                    "screen-with-llm": bool(item.get("screen-with-llm", True)),
                    "mode": str(item.get("mode") or "").strip(),
                }
            )
        if tasks:
            return tasks
    return default_crawl_tasks()


class SchedulerState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.payload = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"tasks": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"tasks": {}}
        return payload if isinstance(payload, dict) else {"tasks": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def should_run(self, task_name: str, interval_minutes: int, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        tasks = self.payload.setdefault("tasks", {})
        task_state = tasks.get(task_name) if isinstance(tasks.get(task_name), dict) else {}
        last_finished_at = task_state.get("last_finished_at_utc")
        if not last_finished_at:
            return True
        try:
            last_time = datetime.fromisoformat(str(last_finished_at))
        except ValueError:
            return True
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        return (now.astimezone(timezone.utc) - last_time.astimezone(timezone.utc)).total_seconds() >= max(1, int(interval_minutes)) * 60

    def mark_finished(self, task_name: str, now: datetime | None = None, report: dict | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        tasks = self.payload.setdefault("tasks", {})
        tasks[task_name] = {
            "last_finished_at_utc": now.astimezone(timezone.utc).isoformat(),
            "last_status": (report or {}).get("status", "ok"),
            "last_report": report or {},
        }
        self.save()


def select_tasks(tasks: Sequence[dict], requested_names: Sequence[str] | None = None) -> list[dict]:
    requested = {str(name).strip() for name in (requested_names or []) if str(name).strip()}
    if not requested:
        return list(tasks)
    return [task for task in tasks if str(task.get("name")) in requested or str(task.get("category")) in requested]


def run_due_tasks(
    config: dict,
    tasks: Sequence[dict],
    state: SchedulerState,
    search_client: Callable[[dict], Sequence[dict]],
    crawl_client: Callable[[dict], dict],
    llm_screen_client: Callable[[dict], dict] | None,
    now: datetime | None = None,
    run_date: str | None = None,
    force: bool = False,
    logger: SchedulerLogger | None = None,
    max_queries_per_task: int | None = None,
    max_results_per_query_override: int | None = None,
) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    logger = logger or SchedulerLogger()
    reports: list[dict] = []
    for task in tasks:
        task_name = str(task.get("name") or task.get("category"))
        interval = int(task.get("interval-minutes") or 60)
        if not force and not state.should_run(task_name, interval, now=now):
            logger.info(
                "task_skip",
                name=task_name,
                reason="interval_not_elapsed",
                interval_minutes=interval,
                now_utc=now.astimezone(timezone.utc).isoformat(),
            )
            continue
        screen = bool(task.get("screen-with-llm", True))
        keywords = task.get("keywords") or []
        # Health check before crawling
        searxng_url = str((config.get("searxng") or {}).get("url") or "http://localhost:11236")
        crawl4ai_url = str((config.get("crawl4ai") or {}).get("url") or "http://localhost:11235")
        if not health_check_searxng(searxng_url) or not health_check_crawl4ai(crawl4ai_url):
            logger.error(
                "task_health_check_failed",
                name=task_name,
                searxng_ok=health_check_searxng(searxng_url),
                crawl4ai_ok=health_check_crawl4ai(crawl4ai_url),
            )
            report = {
                "task": task_name,
                "status": "degraded",
                "reason": "health_check_failed",
                "searxng_ok": health_check_searxng(searxng_url),
                "crawl4ai_ok": health_check_crawl4ai(crawl4ai_url),
            }
            state.mark_finished(task_name, now=now, report=report)
            reports.append(report)
            continue
        logger.info(
            "task_start",
            name=task_name,
            category=task.get("category") or task_name,
            keywords=len(keywords),
            max_queries=task.get("max-queries"),
            screen_with_llm=screen,
            force=force,
        )
        try:
            task_mode = str(task.get("mode") or "").strip()
            if task_mode == "data_driven":
                report = orchestrator.run_data_driven_crawl_pipeline(
                    config=config,
                    search_client=search_client,
                    crawl_client=crawl_client,
                    llm_screen_client=llm_screen_client if screen else None,
                    run_date=run_date,
                    max_queries=max_queries_per_task if max_queries_per_task is not None else task.get("max-queries"),
                    max_results_per_query=max_results_per_query_override if max_results_per_query_override is not None else task.get("max-results-per-query", 3),
                    tick_offset=int(now.timestamp() / 300),
                )
            else:
                report = orchestrator.run_crawl_pipeline(
                config=config,
                keywords=keywords,
                categories=[str(task.get("category") or task_name)],
                search_client=search_client,
                crawl_client=crawl_client,
                llm_screen_client=llm_screen_client if screen else None,
                run_date=run_date,
                max_queries=max_queries_per_task if max_queries_per_task is not None else task.get("max-queries"),
                max_results_per_query=max_results_per_query_override if max_results_per_query_override is not None else task.get("max-results-per-query", 3),
                tick_offset=int(now.timestamp() / 300),
            )
            report = {"task": task_name, **report}
            crawled = report.get("crawled") if isinstance(report.get("crawled"), dict) else {}
            screened = report.get("screened") if isinstance(report.get("screened"), dict) else {}
            data_requirements = report.get("data_requirements") if isinstance(report.get("data_requirements"), dict) else {}
            logger.info(
                "task_success",
                name=task_name,
                crawled=crawled.get("written_count", 0),
                duplicates=crawled.get("duplicate_count", 0),
                screened=screened.get("written_count", 0),
                available_fields=len(data_requirements.get("available") or []),
                missing_fields=len(data_requirements.get("missing") or []),
            )
        except Exception as exc:
            report = {
                "task": task_name,
                "status": "error",
                "error": str(exc),
                "failed_at_utc": now.astimezone(timezone.utc).isoformat(),
            }
            logger.error("task_error", name=task_name, error=str(exc))
        state.mark_finished(task_name, now=now, report=report)
        reports.append(report)
    return reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SoloQuant strategy and finance intelligence crawl scheduler")
    parser.add_argument("--config", default=str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"))
    parser.add_argument("--state-file")
    parser.add_argument("--task", action="append", help="Run only task/category: strategy or finance_intelligence")
    parser.add_argument("--once", action="store_true", help="Run due tasks once and exit")
    parser.add_argument("--daemon", action="store_true", help="Keep polling forever")
    parser.add_argument("--force", action="store_true", help="Ignore interval state and run selected tasks now")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--run-date")
    parser.add_argument("--max-queries-per-task", type=int)
    parser.add_argument("--max-results-per-query", type=int)
    parser.add_argument("--no-llm", action="store_true", help="Crawl and persist raw artifacts without LLM screening")
    parser.add_argument("--list-tasks", action="store_true", help="Print configured crawl tasks without running network calls")
    parser.add_argument("--log-file", help="Append structured scheduler logs to this file")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = orchestrator.load_config(args.config)
    tasks = select_tasks(load_crawl_tasks(config), args.task)
    if args.list_tasks:
        print(json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2))
        return 0

    logger = SchedulerLogger(args.log_file or default_log_path(config))
    logger.info(
        "scheduler_start",
        mode="daemon" if args.daemon else "once",
        config=args.config,
        state_file=args.state_file or default_state_path(config),
        log_file=args.log_file or default_log_path(config),
        tasks=",".join(str(task.get("name")) for task in tasks),
        poll_seconds=args.poll_seconds,
        force=args.force,
        no_llm=args.no_llm,
    )
    client = orchestrator.create_http_client_from_config(config)
    state = SchedulerState(args.state_file or default_state_path(config))
    if args.no_llm:
        tasks = [{**task, "screen-with-llm": False} for task in tasks]

    def tick(force: bool = False) -> list[dict]:
        logger.info("tick_start", force=force, task_count=len(tasks))
        return run_due_tasks(
            config=config,
            tasks=tasks,
            state=state,
            search_client=client.search,
            crawl_client=client.crawl,
            llm_screen_client=client.screen_with_llm,
            run_date=args.run_date,
            force=force,
            logger=logger,
            max_queries_per_task=args.max_queries_per_task,
            max_results_per_query_override=args.max_results_per_query,
        )

    if args.daemon:
        while True:
            reports = tick(force=args.force)
            logger.info("tick_complete", reports=len(reports), sleep_seconds=max(1, int(args.poll_seconds)))
            if reports:
                print(json.dumps({"reports": reports}, ensure_ascii=False, indent=2), flush=True)
            time.sleep(max(1, int(args.poll_seconds)))

    reports = tick(force=args.force or args.once)
    logger.info("scheduler_complete", reports=len(reports))
    print(json.dumps({"reports": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
