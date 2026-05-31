#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
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


PIPELINE_STAGES = (
    "ingest_local_strategies",
    "data_driven_crawl",
    "crawl_research",
    "prepare_reproduction",
    "prepare_iv_data",
    "build_event_graph",
    "build_event_signals",
    "reproduce_one",
    "materialize_variants",
    "optimize_backtests",
    "prepare_live_market_data",
    "update_lifecycle",
    "run_live_paper",
    "export_influx",
)


def default_state_path(config: dict) -> Path:
    return Path(config["workflow-root"]) / "soloquant-pipeline-state.json"


def default_log_path(config: dict) -> Path:
    return Path(config["workflow-root"]) / "soloquant-pipeline.log"


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_pipeline_due(now: datetime, last_finished_at: str | None, interval_seconds: int = 300) -> bool:
    last_time = parse_timestamp(last_finished_at)
    if last_time is None:
        return True
    return (now.astimezone(timezone.utc) - last_time).total_seconds() >= max(1, int(interval_seconds))


LLM_BACKGROUND_STAGES = {"crawl_research", "prepare_reproduction", "reproduce_one", "optimize_backtests"}


def process_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _background_pid_dir(config: dict) -> Path:
    return Path(config.get("workflow-root", "Results/soloquant")) / "llm-background"


def _background_pid_path(stage_name: str, config: dict) -> Path:
    return _background_pid_dir(config) / f"{stage_name}.pid.json"


def _load_background_pid(stage_name: str, config: dict) -> dict:
    path = _background_pid_path(stage_name, config)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_background_process_running(
    stage_name: str,
    config: dict,
    is_alive: Callable[[int], bool] = process_alive,
) -> bool:
    info = _load_background_pid(stage_name, config)
    pid = orchestrator.safe_int(info.get("pid"), 0)
    return pid > 0 and is_alive(pid)


def _cleanup_finished_background(stage_name: str, config: dict) -> None:
    info = _load_background_pid(stage_name, config)
    pid = orchestrator.safe_int(info.get("pid"), 0)
    if pid > 0 and not process_alive(pid):
        path = _background_pid_path(stage_name, config)
        if path.exists():
            path.unlink()


def _launch_background_command(
    command: list[str],
    stage_name: str,
    config: dict,
    run_date: str | None = None,
    popen=subprocess.Popen,
) -> dict:
    pid_dir = _background_pid_dir(config)
    pid_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = pid_dir / f"{stage_name}.out"
    stderr_path = pid_dir / f"{stage_name}.err"
    env = _build_subprocess_env(config)
    stdout_handle = stdout_path.open("ab")
    stderr_handle = stderr_path.open("ab")
    try:
        proc = popen(command, cwd=str(orchestrator.repo_root()), stdout=stdout_handle, stderr=stderr_handle, start_new_session=True, env=env)
    finally:
        stdout_handle.close()
        stderr_handle.close()
    info = {
        "stage": stage_name,
        "pid": int(proc.pid),
        "command": command,
        "cwd": str(orchestrator.repo_root()),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    pid_path = _background_pid_path(stage_name, config)
    pid_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


class PipelineState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.payload = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"runs": [], "last_finished_at_utc": None, "last_status": None}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"runs": [], "last_finished_at_utc": None, "last_status": None}
        return payload if isinstance(payload, dict) else {"runs": [], "last_finished_at_utc": None, "last_status": None}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def last_finished_at(self) -> str | None:
        value = self.payload.get("last_finished_at_utc")
        return str(value) if value else None

    def stage_last_run_at(self, stage_name: str) -> str | None:
        value = (self.payload.get("stage_timestamps") or {}).get(stage_name)
        return str(value) if value else None

    def record_stage_run(self, stage_name: str, timestamp: str) -> None:
        self.payload.setdefault("stage_timestamps", {})[stage_name] = timestamp
        self.save()

    def record_run(self, report: dict) -> None:
        runs = self.payload.setdefault("runs", [])
        if not isinstance(runs, list):
            runs = []
            self.payload["runs"] = runs
        runs.append(report)
        self.payload["runs"] = runs[-100:]
        self.payload["last_status"] = report.get("status")
        self.payload["last_finished_at_utc"] = report.get("finished_at_utc")
        self.save()


class PipelineLogger:
    def __init__(self, log_path: str | Path | None = None):
        self.log_path = Path(log_path) if log_path else None

    def info(self, event: str, **fields) -> None:
        self._write("INFO", event, fields)

    def error(self, event: str, **fields) -> None:
        self._write("ERROR", event, fields)

    def _write(self, level: str, event: str, fields: dict) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        suffix = " ".join(f"{key}={json.dumps(value, ensure_ascii=False) if isinstance(value, str) and any(ch.isspace() for ch in value) else value}" for key, value in fields.items())
        line = f"{timestamp} level={level} event={event}"
        if suffix:
            line = f"{line} {suffix}"
        print(line, flush=True)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")


def _build_subprocess_env(config: dict) -> dict | None:
    """Build extra env vars from config so child processes have API keys."""
    env_overrides: dict[str, str] = {}
    llm = config.get("llm") or config.get("glm") or {}
    api_key_env_var = str(llm.get("api-key-env-var") or "LLM_API_KEY")
    api_key = os.getenv(api_key_env_var, "").strip()
    if not api_key:
        api_key = str(llm.get("api-key-default") or "").strip()
    if api_key:
        env_overrides[api_key_env_var] = api_key
    influx = config.get("influxdb") or {}
    influx_token_env_var = str(influx.get("token-env-var") or "INFLUXDB_TOKEN")
    influx_token = os.getenv(influx_token_env_var, "").strip()
    if not influx_token:
        influx_token = str(influx.get("token-default") or "").strip()
    if influx_token:
        env_overrides[influx_token_env_var] = influx_token
    grafana = config.get("grafana") or {}
    grafana_token_env_var = str(grafana.get("token-env-var") or "GRAFANA_TOKEN")
    grafana_token = os.getenv(grafana_token_env_var, "").strip()
    if grafana_token:
        env_overrides[grafana_token_env_var] = grafana_token
    if not env_overrides:
        return None
    full_env = os.environ.copy()
    full_env.update(env_overrides)
    return full_env


def default_command_runner(command: Sequence[str], cwd: str | Path, timeout_seconds: int | None = None, env: dict | None = None) -> int:
    completed = subprocess.run(list(command), cwd=str(cwd), check=False, timeout=timeout_seconds, env=env)
    return int(completed.returncode)


def command_returncode(result) -> int:
    return int(result[0] if isinstance(result, tuple) else result)


def run_orchestrator_command(
    args: Sequence[str],
    config: dict,
    runner: Callable = default_command_runner,
    timeout_seconds: int | None = None,
) -> dict:
    python = sys.executable
    script = orchestrator.repo_root() / "Scripts" / "soloquant_orchestrator.py"
    config_path = orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"
    command = [python, str(script), "--config", str(config_path), *[str(arg) for arg in args]]
    env = _build_subprocess_env(config)
    try:
        returncode = command_returncode(runner(command, orchestrator.repo_root(), timeout_seconds, env=env))
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "error",
            "returncode": 124,
            "command": command,
            "cwd": str(orchestrator.repo_root()),
            "error": f"command timed out after {exc.timeout} seconds",
        }
    except TypeError:
        # Runner doesn't accept env parameter (e.g. test mock); retry without
        returncode = command_returncode(runner(command, orchestrator.repo_root(), timeout_seconds))
    return {
        "status": "ok" if returncode == 0 else "error",
        "returncode": returncode,
        "command": command,
        "cwd": str(orchestrator.repo_root()),
    }


def _lifecycle_to_influx_lines(strategies: list[dict], now: datetime) -> list[str]:
    """Convert strategy lifecycle state to InfluxDB line protocol for Grafana."""
    ts_ns = int(now.timestamp() * 1e9)
    status_code = {"candidate": 0, "serving": 1, "retired": 2}
    lines = []
    for row in strategies:
        if not isinstance(row, dict):
            continue
        sid = orchestrator.escape_influx_key(str(row.get("strategy_id") or "unknown"))
        best = orchestrator.safe_float(row.get("best_score"), None)
        live = orchestrator.safe_float(row.get("live_score") or row.get("lifecycle_score"), None)
        status = str(row.get("status") or "candidate").lower()
        sc = status_code.get(status, 0)

        fields: list[str] = [f"status_code={sc}i"]
        fields.append(f'status_text="{status}"')
        if best is not None:
            fields.append(f"best_score={best}")
        if live is not None:
            fields.append(f"live_score={live}")
        if best is not None and live is not None and abs(best) > 1e-10:
            decay = (live - best) / abs(best)
            degradation = max(0.0, (best - live) / abs(best))
            fields.append(f"decay_ratio={decay}")
            fields.append(f"degradation={degradation}")
        if row.get("retired_at_utc"):
            fields.append(f'retired_at="{row["retired_at_utc"]}"')
        if row.get("retire_reason"):
            fields.append(f'retire_reason="{row["retire_reason"]}"')

        line = f"strategy_lifecycle,strategy_id={sid} {','.join(fields)} {ts_ns}"
        lines.append(line)
    return lines


def export_lifecycle_to_influx(
    registry_path: str | Path,
    influx_config: dict,
    now: datetime | None = None,
    writer=None,
) -> dict:
    """Write strategy lifecycle metrics to InfluxDB."""
    now = now or datetime.now(timezone.utc)
    path = Path(registry_path)
    if not path.exists():
        return {"status": "ok", "written": 0}
    registry = orchestrator.load_json_payload(path)
    strategies = registry.get("strategies") if isinstance(registry.get("strategies"), list) else []
    lines = _lifecycle_to_influx_lines(strategies, now)
    if not lines:
        return {"status": "ok", "written": 0}
    url = str(influx_config.get("url") or "http://localhost:8086")
    org = str(influx_config.get("org") or "lean")
    bucket = str(influx_config.get("bucket") or "quant")
    token_env = str(influx_config.get("token-env-var") or "INFLUXDB_TOKEN")
    token = os.getenv(token_env, "")
    write_fn = writer or orchestrator.write_lines_to_influx
    written = write_fn(lines, url, org, bucket, token)
    return {"status": "ok", "written": written, "lines": len(lines)}


def update_strategy_lifecycle(
    registry_path: str | Path,
    serving_score_threshold: float = 0.0,
    degradation_threshold: float = 0.25,
    now: datetime | None = None,
    influx_config: dict | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    path = Path(registry_path)
    registry = orchestrator.load_json_payload(path) if path.exists() else {"strategies": []}
    strategies = registry.get("strategies") if isinstance(registry.get("strategies"), list) else []

    serving_count = 0
    retired_count = 0
    candidate_count = 0
    updated_strategies: list[dict] = []
    for strategy in strategies:
        if not isinstance(strategy, dict):
            continue
        row = dict(strategy)
        best_score = orchestrator.safe_float(row.get("best_score"), None)
        live_score = orchestrator.safe_float(row.get("live_score"), None)
        if live_score is None:
            live_summary = {}
            live_config = row.get("live-paper-config") or row.get("live_paper_config")
            if live_config:
                live_summary = orchestrator._load_lean_summary_from_config(live_config)
            live_score = orchestrator.safe_float(live_summary.get("score"), None)
            if live_score is not None:
                row["live_score"] = live_score

        if live_score is None:
            # No real live paper data yet — keep as candidate regardless of best_score
            row["status"] = "candidate"
            row["lifecycle_updated_at_utc"] = now.astimezone(timezone.utc).isoformat()
            candidate_count += 1
            updated_strategies.append(row)
            continue

        score_value = best_score if best_score is not None else float("-inf")
        live_value = live_score
        degraded = best_score is not None and (float(best_score) - float(live_value)) > abs(float(best_score)) * float(degradation_threshold)

        if degraded:
            row["status"] = "retired"
            row["retired_at_utc"] = row.get("retired_at_utc") or now.astimezone(timezone.utc).isoformat()
            row["retire_reason"] = "live_score_degraded"
            retired_count += 1
        elif score_value >= float(serving_score_threshold):
            row["status"] = "serving"
            row["serving_since_utc"] = row.get("serving_since_utc") or now.astimezone(timezone.utc).isoformat()
            row.pop("retire_reason", None)
            serving_count += 1
        else:
            row["status"] = "candidate"
            candidate_count += 1
        row["lifecycle_score"] = live_value
        row["lifecycle_updated_at_utc"] = now.astimezone(timezone.utc).isoformat()
        updated_strategies.append(row)

    registry["strategies"] = sorted(
        updated_strategies,
        key=lambda item: (str(item.get("status") or ""), -(orchestrator.safe_float(item.get("lifecycle_score"), 0.0) or 0.0), str(item.get("strategy_id") or "")),
    )
    orchestrator.write_json_payload(path, registry)

    # Export lifecycle metrics to InfluxDB for Grafana
    if influx_config:
        try:
            export_lifecycle_to_influx(path, influx_config, now=now)
        except Exception as exc:
            import logging
            logging.getLogger("soloquant").warning("lifecycle influx export failed: %s", exc)

    return {
        "status": "ok",
        "strategy_count": len(updated_strategies),
        "serving_count": serving_count,
        "retired_count": retired_count,
        "candidate_count": candidate_count,
    }


def load_pid_file(path: str | Path) -> dict:
    pid_path = Path(path)
    if not pid_path.exists():
        return {}
    try:
        payload = json.loads(pid_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def start_registered_live_paper_strategies(
    registry_path: str | Path,
    process_root: str | Path | None = None,
    popen=subprocess.Popen,
    process_alive: Callable[[int], bool] = process_alive,
    extra_env: dict | None = None,
) -> dict:
    if not Path(registry_path).exists():
        return {
            "status": "ok",
            "started_count": 0,
            "skipped_count": 0,
            "started": [],
            "skipped": [{"reason": "registry_missing", "registry_file": str(registry_path)}],
            "process_root": str(process_root or ""),
        }
    registry = orchestrator.load_json_payload(registry_path)
    strategies = registry.get("strategies") if isinstance(registry.get("strategies"), list) else []
    root = Path(process_root or (Path(registry_path).resolve().parent / "live-paper-processes"))
    root.mkdir(parents=True, exist_ok=True)
    started: list[dict] = []
    skipped: list[dict] = []

    for strategy in strategies:
        if not isinstance(strategy, dict):
            continue
        strategy_id = str(strategy.get("strategy_id") or strategy.get("strategy-id") or "").strip()
        status = str(strategy.get("status") or "candidate").strip().lower()
        live_config = strategy.get("live-paper-config") or strategy.get("live_paper_config")
        if not strategy_id or not live_config:
            skipped.append({"strategy_id": strategy_id, "reason": "missing_strategy_id_or_live_config"})
            continue
        if status == "retired":
            skipped.append({"strategy_id": strategy_id, "reason": "retired"})
            continue

        pid_file = root / f"{orchestrator.safe_slug(strategy_id, 'strategy')}.pid.json"
        existing = load_pid_file(pid_file)
        existing_pid = orchestrator.safe_int(existing.get("pid"), 0)
        if existing_pid > 0 and process_alive(existing_pid):
            skipped.append({"strategy_id": strategy_id, "reason": "already_running", "pid": existing_pid})
            continue

        command, cwd = orchestrator.build_lean_launcher_command(live_config)
        stdout_path = root / f"{orchestrator.safe_slug(strategy_id, 'strategy')}.out"
        stderr_path = root / f"{orchestrator.safe_slug(strategy_id, 'strategy')}.err"
        popen_env = None
        if extra_env:
            popen_env = os.environ.copy()
            popen_env.update(extra_env)
        stdout_handle = stdout_path.open("ab")
        stderr_handle = stderr_path.open("ab")
        try:
            process = popen(command, cwd=str(cwd), stdout=stdout_handle, stderr=stderr_handle, start_new_session=True, env=popen_env)
        finally:
            stdout_handle.close()
            stderr_handle.close()
        payload = {
            "strategy_id": strategy_id,
            "status": status,
            "pid": int(process.pid),
            "command": command,
            "cwd": str(cwd),
            "live-paper-config": str(Path(live_config).resolve()),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        orchestrator.write_json_payload(pid_file, payload)
        started.append(payload)

    return {
        "status": "ok",
        "started_count": len(started),
        "skipped_count": len(skipped),
        "started": started,
        "skipped": skipped,
        "process_root": str(root),
    }


def prepare_registered_live_configs_for_market_data(
    registry_path: str | Path,
    snapshot_file: str | Path,
) -> dict:
    if not Path(registry_path).exists():
        return {"status": "ok", "updated_count": 0, "updated": []}
    registry = orchestrator.load_json_payload(registry_path)
    strategies = registry.get("strategies") if isinstance(registry.get("strategies"), list) else []
    updated: list[dict] = []
    for strategy in strategies:
        if not isinstance(strategy, dict) or str(strategy.get("status") or "").lower() == "retired":
            continue
        live_config = strategy.get("live-paper-config") or strategy.get("live_paper_config")
        if not live_config:
            continue
        config_path = Path(str(live_config))
        if not config_path.exists():
            continue
        payload = orchestrator.load_json_payload(config_path)
        parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
        next_parameters = {
            **parameters,
            "live-price-snapshot-file": str(snapshot_file),
            "live-price-source-mode": "auto",
            "shared-live-market-snapshot-file": str(snapshot_file),
        }
        payload["parameters"] = next_parameters
        orchestrator.write_json_payload(config_path, payload)
        updated.append({"strategy_id": strategy.get("strategy_id"), "live-paper-config": str(config_path), "snapshot_file": str(snapshot_file)})
    return {"status": "ok", "updated_count": len(updated), "updated": updated}


class DefaultPipelineServices:
    def __init__(
        self,
        command_runner: Callable[[Sequence[str], str | Path, int | None], int] = default_command_runner,
        timeout_seconds: int | None = None,
        live_popen=subprocess.Popen,
        live_process_alive: Callable[[int], bool] = process_alive,
        mode: str = "work",
    ):
        self.command_runner = command_runner
        self.timeout_seconds = 900 if timeout_seconds is None else timeout_seconds
        self.live_popen = live_popen
        self.live_process_alive = live_process_alive
        self._state: PipelineState | None = None
        self.mode = mode

    def set_state(self, state: PipelineState) -> None:
        self._state = state

    def ingest_local_strategies(self, config: dict, run_date: str | None = None) -> dict:
        local_dir = Path(config["workflow-root"]) / "local-strategies"
        return orchestrator.ingest_local_strategies(
            local_dir=local_dir,
            artifact_root=config["artifact-root"],
            run_date=run_date,
        )

    def data_driven_crawl(self, config: dict, run_date: str | None = None) -> dict:
        pipeline_config = config.get("pipeline") if isinstance(config.get("pipeline"), dict) else {}
        interval_seconds = max(60, orchestrator.safe_int(pipeline_config.get("data-driven-crawl-interval-seconds"), 28800))
        state = self._state
        if state is not None and not state.should_run("data_driven_crawl", interval_seconds):
            return {"status": "ok", "action": "interval_not_elapsed", "interval_seconds": interval_seconds}
        if self.mode == "debug":
            report = self._data_driven_crawl_sync(config, run_date=run_date)
        else:
            command = [
                sys.executable,
                str(orchestrator.repo_root() / "Scripts" / "soloquant_crawl_scheduler.py"),
                "--config",
                str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"),
                "--task",
                "data_driven_strategy",
                "--force",
                "--once",
                "--max-queries-per-task",
                str(max(1, orchestrator.safe_int(pipeline_config.get("crawl-max-queries-per-task"), 4))),
                "--max-results-per-query",
                str(max(1, orchestrator.safe_int(pipeline_config.get("crawl-max-results-per-query"), 2))),
            ]
            if run_date:
                command.extend(["--run-date", str(run_date)])
            try:
                info = _launch_background_command(command, "data_driven_crawl", config, run_date=run_date, popen=self.live_popen)
                report = {"status": "ok", "mode": "background", "action": "launched", "pid": info["pid"]}
            except Exception as exc:
                report = {"status": "error", "error": str(exc)}
        if state is not None:
            state.mark_finished("data_driven_crawl", report=report)
        return report

    def _data_driven_crawl_sync(self, config: dict, run_date: str | None = None) -> dict:
        client = orchestrator.create_http_client_from_config(config)
        pipeline_config = config.get("pipeline") if isinstance(config.get("pipeline"), dict) else {}
        return orchestrator.run_data_driven_crawl_pipeline(
            config=config,
            search_client=client.search,
            crawl_client=client.crawl,
            llm_screen_client=client.screen_with_llm,
            run_date=run_date,
            max_queries=max(1, orchestrator.safe_int(pipeline_config.get("crawl-max-queries-per-task"), 4)),
            max_results_per_query=max(1, orchestrator.safe_int(pipeline_config.get("crawl-max-results-per-query"), 2)),
        )

    def crawl_research(self, config: dict, run_date: str | None = None) -> dict:
        if self.mode == "debug":
            return self._crawl_research_sync(config, run_date=run_date)
        if _is_background_process_running("crawl_research", config, is_alive=self.live_process_alive):
            return {"status": "ok", "mode": "background", "action": "already_running"}
        _cleanup_finished_background("crawl_research", config)
        pipeline_config = config.get("pipeline") if isinstance(config.get("pipeline"), dict) else {}
        command = [
            sys.executable,
            str(orchestrator.repo_root() / "Scripts" / "soloquant_crawl_scheduler.py"),
            "--config",
            str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"),
            "--task",
            "strategy",
            "--task",
            "finance_intelligence",
            "--force",
            "--once",
            "--max-queries-per-task",
            str(max(1, orchestrator.safe_int(pipeline_config.get("crawl-max-queries-per-task"), 2))),
            "--max-results-per-query",
            str(max(1, orchestrator.safe_int(pipeline_config.get("crawl-max-results-per-query"), 1))),
        ]
        if run_date:
            command.extend(["--run-date", str(run_date)])
        try:
            info = _launch_background_command(command, "crawl_research", config, run_date=run_date, popen=self.live_popen)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "mode": "background", "action": "launched", "pid": info["pid"]}

    def prepare_reproduction(self, config: dict, run_date: str | None = None) -> dict:
        if self.mode == "debug":
            return self._prepare_reproduction_sync(config, run_date=run_date)
        launched = []
        for stage_key, flag in [("prepare_reproduction", "--prepare-reproduction"), ("analyze_finance_intelligence", "--analyze-finance-intelligence")]:
            if _is_background_process_running(stage_key, config, is_alive=self.live_process_alive):
                launched.append({"stage_key": stage_key, "action": "already_running"})
                continue
            _cleanup_finished_background(stage_key, config)
            args = [flag, "--max-items", "1"]
            if run_date:
                args.extend(["--run-date", str(run_date)])
            python = sys.executable
            script = orchestrator.repo_root() / "Scripts" / "soloquant_orchestrator.py"
            config_path = orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"
            command = [python, str(script), "--config", str(config_path), *[str(a) for a in args]]
            try:
                info = _launch_background_command(command, stage_key, config, run_date=run_date, popen=self.live_popen)
                launched.append({"stage_key": stage_key, "action": "launched", "pid": info["pid"]})
            except Exception as exc:
                launched.append({"stage_key": stage_key, "action": "error", "error": str(exc)})
        return {"status": "ok", "mode": "background", "launched": launched}

    def prepare_iv_data(self, config: dict, run_date: str | None = None) -> dict:
        data_config = config.get("data") if isinstance(config.get("data"), dict) else {}
        tushare_path = data_config.get("tushare-data-path") or "/home/project/tushare-downloader/tushare_data"
        output_root = str(orchestrator.repo_root() / "Data" / "alternative" / "ashare-implied-volatility")
        command = [
            sys.executable, str(CURRENT_DIR / "export_ashare_implied_volatility_data.py"),
            "--tushare-data-path", tushare_path,
            "--output-root", output_root,
        ]
        if run_date:
            command.extend(["--start-date", str(run_date)])
        env = _build_subprocess_env(config)
        try:
            returncode = command_returncode(self.command_runner(command, orchestrator.repo_root(), self.timeout_seconds, env=env))
        except TypeError:
            returncode = command_returncode(self.command_runner(command, orchestrator.repo_root(), self.timeout_seconds))
        status = "ok" if returncode == 0 else "error"
        return {"status": status, "returncode": returncode}

    def build_event_graph(self, config: dict, run_date: str | None = None) -> dict:
        args = ["--build-finance-event-graph"]
        if run_date:
            args.extend(["--run-date", str(run_date)])
        return run_orchestrator_command(args, config, self.command_runner, self.timeout_seconds)

    def build_event_signals(self, config: dict, run_date: str | None = None) -> dict:
        args = ["--build-event-signal-context"]
        if run_date:
            args.extend(["--run-date", str(run_date)])
        return run_orchestrator_command(args, config, self.command_runner, self.timeout_seconds)

    def reproduce_one(self, config: dict, run_date: str | None = None) -> dict:
        """Launch one full reproduction cycle:
        generate code → compile → smoke test. In work mode runs as
        background Popen; in debug mode runs synchronously."""
        if self.mode == "debug":
            return self._reproduce_one_sync(config, run_date=run_date)
        if _is_background_process_running("reproduce_one", config, is_alive=self.live_process_alive):
            return {"status": "ok", "mode": "background", "action": "already_running"}
        _cleanup_finished_background("reproduce_one", config)
        python = sys.executable
        script = orchestrator.repo_root() / "Scripts" / "soloquant_orchestrator.py"
        config_path = orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"
        command = [
            python, str(script), "--config", str(config_path),
            "--generate-strategy-implementations", "--max-items", "1",
            "--smoke-test-strategies",
        ]
        if run_date:
            command.extend(["--run-date", str(run_date)])
        try:
            info = _launch_background_command(command, "reproduce_one", config, run_date=run_date, popen=self.live_popen)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "mode": "background", "action": "launched", "pid": info["pid"]}

    def compile_strategies(self, config: dict) -> dict:
        languages = (config.get("strategy-policy") or {}).get("languages")
        if isinstance(languages, list) and languages:
            lang_list = [str(l).strip() for l in languages if str(l).strip()]
        else:
            lang_list = [str((config.get("strategy-policy") or {}).get("language") or "CSharp")]
        combined: dict = {"status": "ok", "languages": []}
        for lang in lang_list:
            is_python = lang.strip().lower() in {"python", "py"}
            if is_python:
                py_root = orchestrator.repo_root() / "Algorithm.Python" / "SoloQuantGenerated"
                syntax_errors: list[str] = []
                if py_root.exists():
                    for py_file in sorted(py_root.rglob("*.py")):
                        try:
                            compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")
                        except SyntaxError as exc:
                            syntax_errors.append(f"{py_file}: {exc}")
                if syntax_errors:
                    combined["languages"].append({"language": "Python", "status": "error", "syntax_errors": syntax_errors})
                    combined["status"] = "error"
                else:
                    combined["languages"].append({"language": "Python", "status": "ok", "syntax_errors": []})
            else:
                dotnet = str((config.get("lean") or {}).get("dotnet-binary") or "/usr/local/dotnet/dotnet")
                command = [dotnet, "build", "Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj", "-c", "Debug"]
                env = _build_subprocess_env(config)
                try:
                    returncode = command_returncode(self.command_runner(command, orchestrator.repo_root(), self.timeout_seconds, env=env))
                except TypeError:
                    returncode = command_returncode(self.command_runner(command, orchestrator.repo_root(), self.timeout_seconds))
                broken_files = []
                if returncode != 0:
                    gen_root = orchestrator.repo_root() / "Algorithm.CSharp" / "SoloQuantGenerated"
                    build_output = self._last_build_output(command, dotnet)
                    broken_paths = orchestrator.parse_broken_cs_files(build_output, gen_root)
                    if broken_paths:
                        for cs_file in broken_paths:
                            broken_dest = cs_file.with_suffix(".cs.broken")
                            if cs_file.exists():
                                cs_file.rename(broken_dest)
                            broken_files.append(str(cs_file))
                    elif gen_root.exists():
                        build_output_fallback = self._last_build_output(command, dotnet)
                        for cs_file in sorted(gen_root.rglob("*.cs")):
                            if str(cs_file) in build_output_fallback or str(cs_file.resolve()) in build_output_fallback:
                                broken_dest = cs_file.with_suffix(".cs.broken")
                                if cs_file.exists():
                                    cs_file.rename(broken_dest)
                                broken_files.append(str(cs_file))
                    if broken_files:
                        try:
                            returncode = command_returncode(self.command_runner(command, orchestrator.repo_root(), self.timeout_seconds, env=env))
                        except TypeError:
                            returncode = command_returncode(self.command_runner(command, orchestrator.repo_root(), self.timeout_seconds))
                lang_result = {"language": "CSharp", "status": "ok" if returncode == 0 else "error", "returncode": returncode, "broken_files": broken_files}
                combined["languages"].append(lang_result)
                if returncode != 0:
                    combined["status"] = "error"
        return combined

    def _last_build_output(self, command: list[str], dotnet: str) -> str:
        """Capture dotnet build stdout+stderr for error parsing."""
        try:
            result = subprocess.run(list(command), cwd=str(orchestrator.repo_root()), capture_output=True, text=True, timeout=120)
            return (result.stdout or "") + (result.stderr or "")
        except Exception:
            return ""

    def smoke_test_strategies(self, config: dict) -> dict:
        return run_orchestrator_command(["--smoke-test-strategies"], config, self.command_runner, self.timeout_seconds)

    def materialize_variants(self, config: dict, run_date: str | None = None) -> dict:
        lang = str((config.get("strategy-policy") or {}).get("language") or "CSharp")
        args = ["--materialize-generated-strategies", "--language", lang, "--start-date", "2020-01-01", "--end-date", "2025-12-31"]
        if run_date:
            args.extend(["--run-date", str(run_date)])
        return run_orchestrator_command(args, config, self.command_runner, self.timeout_seconds)

    def optimize_backtests(self, config: dict, run_date: str | None = None) -> dict:
        if self.mode == "debug":
            return self._optimize_backtests_sync(config, run_date=run_date)
        if _is_background_process_running("optimize_backtests", config, is_alive=self.live_process_alive):
            return {"status": "ok", "mode": "background", "action": "already_running"}
        _cleanup_finished_background("optimize_backtests", config)
        python = sys.executable
        script = orchestrator.repo_root() / "Scripts" / "soloquant_orchestrator.py"
        config_path = orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"
        command = [
            python, str(script), "--config", str(config_path),
            "--optimize-strategies", "--max-items", "1",
        ]
        if run_date:
            command.extend(["--run-date", str(run_date)])
        try:
            info = _launch_background_command(command, "optimize_backtests", config, run_date=run_date, popen=self.live_popen)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "mode": "background", "action": "launched", "pid": info["pid"]}

    def prepare_live_market_data(self, config: dict, now: datetime | None = None) -> dict:
        import ashare_live_market_cache

        now = now or datetime.now(timezone.utc)
        data_config = config.get("data") if isinstance(config.get("data"), dict) else {}
        market_config = {
            "tushare-data-path": data_config.get("tushare-data-path") or "/home/project/tushare-downloader/tushare_data",
            "timezone": "Asia/Shanghai",
            "shared-live-market-snapshot-file": str(orchestrator.repo_root() / "Results" / "shared-live-market" / "ashare-live-price-snapshot.json"),
            "shared-live-market-report-file": str(orchestrator.repo_root() / "Results" / "shared-live-market" / "ashare-live-market-report.json"),
            "shared-live-market-archive-path": str(orchestrator.repo_root() / "Data" / "archive" / "ashare-live-market-daily-quotes"),
            "shared-live-market-refresh-interval-seconds": 60,
            "shared-live-market-batch-size": 200,
            "shared-live-market-max-workers": 1,
            "shared-live-market-max-requests-per-minute": 50,
            "live-price-source-mode": "auto",
        }
        context = ashare_live_market_cache.resolve_market_data_context(
            market_config["tushare-data-path"],
            requested_mode=market_config["live-price-source-mode"],
            now=now,
            timezone=market_config["timezone"],
        )
        simulated_client = ashare_live_market_cache.create_simulated_client(market_config)
        realtime_client = ashare_live_market_cache.create_realtime_client(market_config) if context.get("selected_mode") == "tushare-realtime" else None
        report = ashare_live_market_cache.ensure_full_market_snapshot(
            market_config,
            trade_date=ashare_live_market_cache.resolve_trade_date(market_config["tushare-data-path"], now=now, timezone=market_config["timezone"]),
            market_context=context,
            realtime_client=realtime_client,
            simulated_client=simulated_client,
        )
        return {
            "status": report.get("status", "ok"),
            "source_mode": context.get("selected_mode"),
            "market_open": bool(context.get("market_open")),
            "session_state": context.get("session_state"),
            "snapshot_file": str(market_config["shared-live-market-snapshot-file"]),
            "report": report.get("report", {}),
        }

    def run_live_paper(self, config: dict) -> dict:
        extra_env = {}
        influx = config.get("influxdb") or {}
        influx_token_env_var = str(influx.get("token-env-var") or "INFLUXDB_TOKEN")
        influx_token = os.getenv(influx_token_env_var, "").strip()
        if influx_token:
            extra_env[influx_token_env_var] = influx_token
        return start_registered_live_paper_strategies(
            config["registry-file"],
            popen=self.live_popen,
            process_alive=self.live_process_alive,
            extra_env=extra_env or None,
        )

    def update_lifecycle(self, config: dict) -> dict:
        prepare_registered_live_configs_for_market_data(
            config["registry-file"],
            orchestrator.repo_root() / "Results" / "shared-live-market" / "ashare-live-price-snapshot.json",
        )
        return update_strategy_lifecycle(
            config["registry-file"],
            serving_score_threshold=float((config.get("strategy-policy") or {}).get("serving-score-threshold", 0.0)),
            degradation_threshold=float((config.get("strategy-policy") or {}).get("degradation-threshold", 0.25)),
            influx_config=config.get("influxdb"),
        )

    def export_influx(self, config: dict, run_date: str | None = None) -> dict:
        reports = []
        for args in (
            ["--export-research-influx"],
            ["--export-finance-event-graph-influx"],
            ["--export-strategy-results-influx"],
            ["--export-strategy-pipeline"],
        ):
            command_args = list(args)
            if run_date and args[0] != "--export-strategy-results-influx":
                command_args.extend(["--run-date", str(run_date)])
            reports.append(run_orchestrator_command(command_args, config, self.command_runner, self.timeout_seconds))
        status = "ok" if all(report.get("status") == "ok" for report in reports) else "error"
        return {"status": status, "reports": reports}

    # -- Synchronous implementations for debug mode --

    def _crawl_research_sync(self, config: dict, run_date: str | None = None) -> dict:
        pipeline_config = config.get("pipeline") if isinstance(config.get("pipeline"), dict) else {}
        args = [
            "--task", "strategy",
            "--task", "finance_intelligence",
            "--force", "--once",
            "--max-queries-per-task", str(max(1, orchestrator.safe_int(pipeline_config.get("crawl-max-queries-per-task"), 2))),
            "--max-results-per-query", str(max(1, orchestrator.safe_int(pipeline_config.get("crawl-max-results-per-query"), 1))),
        ]
        if run_date:
            args.extend(["--run-date", str(run_date)])
        python = sys.executable
        script = str(orchestrator.repo_root() / "Scripts" / "soloquant_crawl_scheduler.py")
        config_path = str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json")
        command = [python, script, "--config", config_path, *args]
        env = _build_subprocess_env(config)
        try:
            result = subprocess.run(command, cwd=str(orchestrator.repo_root()), capture_output=True, text=True, timeout=self.timeout_seconds, env=env)
        except subprocess.TimeoutExpired as exc:
            return {"status": "error", "mode": "debug", "error": f"timed out after {exc.timeout}s"}
        status = "ok" if result.returncode == 0 else "error"
        return {"status": status, "mode": "debug", "returncode": result.returncode}

    def _prepare_reproduction_sync(self, config: dict, run_date: str | None = None) -> dict:
        results = []
        for flag in ["--prepare-reproduction", "--analyze-finance-intelligence"]:
            args = [flag, "--max-items", "1"]
            if run_date:
                args.extend(["--run-date", str(run_date)])
            report = run_orchestrator_command(args, config, self.command_runner, self.timeout_seconds)
            results.append({"flag": flag, "status": report.get("status"), "returncode": report.get("returncode")})
        status = "ok" if all(r.get("status") == "ok" for r in results) else "error"
        return {"status": status, "mode": "debug", "results": results}

    def _reproduce_one_sync(self, config: dict, run_date: str | None = None) -> dict:
        args = ["--generate-strategy-implementations", "--max-items", "1", "--smoke-test-strategies"]
        if run_date:
            args.extend(["--run-date", str(run_date)])
        report = run_orchestrator_command(args, config, self.command_runner, self.timeout_seconds)
        return {**report, "mode": "debug"}

    def _optimize_backtests_sync(self, config: dict, run_date: str | None = None) -> dict:
        args = ["--optimize-strategies", "--max-items", "1"]
        if run_date:
            args.extend(["--run-date", str(run_date)])
        report = run_orchestrator_command(args, config, self.command_runner, self.timeout_seconds)
        return {**report, "mode": "debug"}


def run_pipeline_tick(
    config: dict,
    services=None,
    run_date: str | None = None,
    now: datetime | None = None,
    logger: PipelineLogger | None = None,
    state: PipelineState | None = None,
    mode: str = "work",
) -> dict:
    services = services or DefaultPipelineServices(mode=mode)
    if state is not None and isinstance(services, DefaultPipelineServices):
        services.set_state(state)
    now = now or datetime.now(timezone.utc)
    logger = logger or PipelineLogger()
    started_at = datetime.now(timezone.utc)
    report = {
        "status": "ok",
        "started_at_utc": started_at.isoformat(),
        "stages": [],
        "degraded_stages": [],
    }

    pipeline_config = config.get("pipeline") if isinstance(config.get("pipeline"), dict) else {}
    debug_mode = mode == "debug"

    for stage_name in PIPELINE_STAGES:
        # Interval-based skip for stages with dedicated intervals (skip in debug mode)
        if not debug_mode:
            if stage_name == "ingest_local_strategies" and state is not None:
                interval = max(1, orchestrator.safe_int(pipeline_config.get("local-ingest-interval-seconds"), 3600))
                last_run = state.stage_last_run_at(stage_name)
                if last_run and not is_pipeline_due(now, last_run, interval_seconds=interval):
                    logger.info("stage_skip", stage=stage_name, reason="interval_not_elapsed")
                    continue
            if stage_name == "data_driven_crawl" and state is not None:
                interval = max(1, orchestrator.safe_int(pipeline_config.get("data-driven-crawl-interval-seconds"), 28800))
                last_run = state.stage_last_run_at(stage_name)
                if last_run and not is_pipeline_due(now, last_run, interval_seconds=interval):
                    logger.info("stage_skip", stage=stage_name, reason="interval_not_elapsed")
                    continue
            if stage_name == "crawl_research" and state is not None:
                interval = max(1, orchestrator.safe_int(pipeline_config.get("crawl-interval-seconds"), 28800))
                last_run = state.stage_last_run_at(stage_name)
                if last_run and not is_pipeline_due(now, last_run, interval_seconds=interval):
                    logger.info("stage_skip", stage=stage_name, reason="interval_not_elapsed")
                    continue
            if stage_name == "reproduce_one" and state is not None:
                interval = max(1, orchestrator.safe_int(pipeline_config.get("reproduce-interval-seconds"), 1800))
                last_run = state.stage_last_run_at(stage_name)
                if last_run and not is_pipeline_due(now, last_run, interval_seconds=interval):
                    logger.info("stage_skip", stage=stage_name, reason="interval_not_elapsed")
                    continue
            if stage_name == "optimize_backtests" and state is not None:
                interval = max(1, orchestrator.safe_int(pipeline_config.get("optimize-interval-seconds"), 1800))
                last_run = state.stage_last_run_at(stage_name)
                if last_run and not is_pipeline_due(now, last_run, interval_seconds=interval):
                    logger.info("stage_skip", stage=stage_name, reason="interval_not_elapsed")
                    continue

        logger.info("stage_start", stage=stage_name)
        stage_started_at = datetime.now(timezone.utc)
        try:
            if stage_name in {"ingest_local_strategies", "data_driven_crawl", "crawl_research", "prepare_reproduction", "prepare_iv_data", "build_event_graph", "build_event_signals", "reproduce_one", "materialize_variants", "optimize_backtests", "export_influx"}:
                stage_report = getattr(services, stage_name)(config, run_date=run_date)
            elif stage_name == "prepare_live_market_data":
                stage_report = services.prepare_live_market_data(config, now=now)
            else:
                stage_report = getattr(services, stage_name)(config)
        except Exception as exc:
            stage_report = {"status": "error", "error": str(exc)}

        stage_row = {
            "stage": stage_name,
            "status": stage_report.get("status", "ok") if isinstance(stage_report, dict) else "ok",
            "started_at_utc": stage_started_at.isoformat(),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "report": stage_report if isinstance(stage_report, dict) else {"result": stage_report},
        }
        report["stages"].append(stage_row)
        logger.info("stage_complete", stage=stage_name, status=stage_row["status"])

        # Record stage timestamp for interval-based scheduling (skip in debug mode)
        if not debug_mode and state is not None and stage_row["status"] == "ok":
            state.record_stage_run(stage_name, datetime.now(timezone.utc).isoformat())

        if stage_row["status"] != "ok" and stage_name not in LLM_BACKGROUND_STAGES:
            if stage_name in {"ingest_local_strategies", "crawl_research", "prepare_reproduction", "prepare_iv_data", "build_event_graph", "build_event_signals", "reproduce_one", "optimize_backtests", "export_influx"}:
                report["status"] = "degraded"
                report["degraded_stage"] = stage_name
                report["degraded_stages"].append(stage_name)
                continue
            report["status"] = "error"
            report["failed_stage"] = stage_name
            break

    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full SoloQuant automatic end-to-end pipeline")
    parser.add_argument("--config", default=str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"))
    parser.add_argument("--state-file")
    parser.add_argument("--log-file")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=300)
    parser.add_argument("--timeout-seconds", type=int, default=0)
    parser.add_argument("--mode", choices=["debug", "work"], default="work")
    parser.add_argument("--run-date")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = orchestrator.load_config(args.config)
    state = PipelineState(args.state_file or default_state_path(config))
    logger = PipelineLogger(args.log_file or default_log_path(config))
    timeout = args.timeout_seconds if args.timeout_seconds and args.timeout_seconds > 0 else None
    services = DefaultPipelineServices(timeout_seconds=timeout, mode=args.mode)

    def tick(force: bool = False) -> dict:
        now = datetime.now(timezone.utc)
        if not force and args.mode == "work" and not is_pipeline_due(now, state.last_finished_at(), interval_seconds=max(1, int(args.poll_seconds))):
            report = {
                "status": "skipped",
                "reason": "interval_not_elapsed",
                "now_utc": now.isoformat(),
                "last_finished_at_utc": state.last_finished_at(),
                "finished_at_utc": now.isoformat(),
            }
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            return report
        report = run_pipeline_tick(config, services=services, run_date=args.run_date, now=now, logger=logger, state=state, mode=args.mode)
        state.record_run(report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return report

    logger.info(
        "pipeline_start",
        mode=args.mode,
        config=args.config,
        state_file=args.state_file or default_state_path(config),
        poll_seconds=max(1, int(args.poll_seconds)),
        force=args.force,
    )

    if args.daemon:
        while True:
            tick(force=args.force)
            time.sleep(max(1, int(args.poll_seconds)))

    report = tick(force=args.force or args.once)
    return 1 if report.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
