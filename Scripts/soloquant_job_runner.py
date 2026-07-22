#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import soloquant_orchestrator as orchestrator


def default_state_path(config: dict) -> Path:
    return Path(config["workflow-root"]) / "soloquant-job-runner-state.json"


class JobRunnerState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.payload = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"jobs": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"jobs": {}}
        return payload if isinstance(payload, dict) else {"jobs": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def last_finished_at(self, job_name: str) -> str | None:
        jobs = self.payload.setdefault("jobs", {})
        job_state = jobs.get(job_name) if isinstance(jobs.get(job_name), dict) else {}
        value = job_state.get("last_finished_at_utc")
        return str(value) if value else None

    def mark_finished(self, job_name: str, report: dict, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        jobs = self.payload.setdefault("jobs", {})
        jobs[job_name] = {
            "last_finished_at_utc": now.astimezone(timezone.utc).isoformat(),
            "last_status": report.get("status"),
            "last_returncode": report.get("returncode"),
            "last_report": report,
        }
        self.save()


def _parse_int_set(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in str(field).split(","):
        part = part.strip()
        if part == "*":
            values.update(range(minimum, maximum + 1))
        elif part.startswith("*/"):
            step = int(part[2:])
            values.update(range(minimum, maximum + 1, max(1, step)))
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            values.update(range(max(minimum, start), min(maximum, end) + 1))
        elif part:
            values.add(int(part))
    return {value for value in values if minimum <= value <= maximum}


def _cron_matches(cron: str, now: datetime) -> bool:
    parts = str(cron).split()
    if len(parts) != 5:
        return False
    minute, hour, day_of_month, month, day_of_week = parts
    weekday = (now.weekday() + 1) % 7
    return (
        now.minute in _parse_int_set(minute, 0, 59)
        and now.hour in _parse_int_set(hour, 0, 23)
        and now.day in _parse_int_set(day_of_month, 1, 31)
        and now.month in _parse_int_set(month, 1, 12)
        and weekday in _parse_int_set(day_of_week, 0, 6)
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_cron_due(cron: str, now: datetime, last_finished_at: str | None = None) -> bool:
    now = now.astimezone(timezone.utc)
    if not _cron_matches(cron, now):
        return False
    last_time = _parse_timestamp(last_finished_at)
    if last_time is None:
        return True
    return last_time.replace(second=0, microsecond=0) < now.replace(second=0, microsecond=0)


def default_command_runner(command: Sequence[str], cwd: str | Path, timeout_seconds: int | None = None) -> int:
    completed = subprocess.run(list(command), cwd=str(cwd), check=False, timeout=timeout_seconds)
    return int(completed.returncode)


def select_job_specs(specs: Sequence[dict], requested_names: Sequence[str] | None = None) -> list[dict]:
    requested = {str(name).strip() for name in (requested_names or []) if str(name).strip()}
    if not requested:
        return [dict(spec) for spec in specs if isinstance(spec, dict)]
    return [dict(spec) for spec in specs if isinstance(spec, dict) and str(spec.get("name")) in requested]


def mark_long_running_jobs(specs: Sequence[dict]) -> list[dict]:
    long_running_names = {"tushare-incremental-scheduler", "soloquant-live-paper"}
    marked: list[dict] = []
    for spec in specs:
        row = dict(spec)
        row["long-running"] = bool(row.get("long-running") or row.get("name") in long_running_names)
        marked.append(row)
    return marked


def run_due_jobs(
    specs: Sequence[dict],
    state: JobRunnerState,
    runner: Callable[[Sequence[str], str | Path, int | None], int] = default_command_runner,
    now: datetime | None = None,
    force: bool = False,
    include_long_running: bool = False,
    timeout_seconds: int | None = None,
    stop_on_error: bool = True,
) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    reports: list[dict] = []
    for spec in mark_long_running_jobs(specs):
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        cron = str(spec.get("cron") or "* * * * *")
        command = spec.get("command") if isinstance(spec.get("command"), list) else []
        cwd = spec.get("cwd") or orchestrator.repo_root()
        if not command:
            report = {"job": name, "status": "error", "error": "missing command", "returncode": 1}
            state.mark_finished(name, report, now=now)
            reports.append(report)
            if stop_on_error:
                break
            continue
        if spec.get("long-running") and not include_long_running:
            reports.append({"job": name, "status": "skipped", "reason": "long_running_requires_explicit_include"})
            continue
        if not force and not is_cron_due(cron, now, state.last_finished_at(name)):
            reports.append({"job": name, "status": "skipped", "reason": "not_due"})
            continue
        started_at = datetime.now(timezone.utc)
        try:
            returncode = int(runner(command, cwd, timeout_seconds))
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            report = {
                "job": name,
                "status": "error",
                "returncode": returncode,
                "error": f"timeout after {exc.timeout} seconds",
                "started_at_utc": started_at.isoformat(),
                "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            state.mark_finished(name, report, now=now)
            reports.append(report)
            if stop_on_error:
                break
            continue
        status = "success" if returncode == 0 else "error"
        report = {
            "job": name,
            "status": status,
            "returncode": returncode,
            "command": [str(value) for value in command],
            "cwd": str(cwd),
            "started_at_utc": started_at.isoformat(),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        state.mark_finished(name, report, now=now)
        reports.append(report)
        if status == "error" and stop_on_error:
            break
    return reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SoloQuant job specs with local state")
    parser.add_argument("--config", default=str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"))
    parser.add_argument("--state-file")
    parser.add_argument("--job", action="append", help="Run only the named job")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--timeout-seconds", type=int, default=0)
    parser.add_argument("--include-long-running", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--list-jobs", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = orchestrator.load_config(args.config)
    specs = select_job_specs(orchestrator.build_job_specs(config), args.job)
    specs = mark_long_running_jobs(specs)
    if args.list_jobs:
        print(json.dumps({"jobs": specs}, ensure_ascii=False, indent=2))
        return 0

    state = JobRunnerState(args.state_file or default_state_path(config))
    timeout = args.timeout_seconds if args.timeout_seconds and args.timeout_seconds > 0 else None

    def tick() -> list[dict]:
        return run_due_jobs(
            specs,
            state,
            force=args.force or args.once,
            include_long_running=args.include_long_running,
            timeout_seconds=timeout,
            stop_on_error=not args.continue_on_error,
        )

    if args.daemon:
        while True:
            reports = tick()
            print(json.dumps({"reports": reports}, ensure_ascii=False, indent=2), flush=True)
            time.sleep(max(1, int(args.poll_seconds)))

    reports = tick()
    print(json.dumps({"reports": reports}, ensure_ascii=False, indent=2))
    return 1 if any(report.get("status") == "error" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
