#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessSpec:
    command: list[str]
    cwd: Path
    label: str
    passthrough: bool = False


def default_bridge_python_executable() -> str:
    preferred = Path("/root/miniconda3/envs/quant311/bin/python")
    return str(preferred) if preferred.exists() else sys.executable


def dotnet_binary_path() -> str:
    preferred = Path("/usr/local/dotnet/dotnet")
    return str(preferred) if preferred.exists() else "dotnet"


def normalize_console_output_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    return "custom" if mode == "custom" else "native"


def _normalize_process_output_line(line: str, label: str, passthrough: bool) -> str | None:
    text = line.rstrip("\n")
    if not text.strip():
        return None
    if passthrough:
        return text
    return f"[{label}] {text}"


def stream_process_output(process: subprocess.Popen, label: str, passthrough: bool = False) -> threading.Thread | None:
    if process.stdout is None:
        return None

    def consume_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            normalized = _normalize_process_output_line(line, label=label, passthrough=passthrough)
            if normalized:
                print(normalized, flush=True)

    thread = threading.Thread(target=consume_output, name=f"{label}-output", daemon=True)
    thread.start()
    return thread


def start_logged_process(spec: ProcessSpec) -> tuple[subprocess.Popen, threading.Thread | None]:
    process = subprocess.Popen(
        spec.command,
        cwd=spec.cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    print(f"[process start] name={spec.label} pid={process.pid} cwd={spec.cwd}", flush=True)
    print(f"[process cmd] {spec.label}: {' '.join(spec.command)}", flush=True)
    return process, stream_process_output(process, label=spec.label, passthrough=spec.passthrough)


def terminate_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def join_output_thread(thread: threading.Thread | None) -> None:
    if thread is None:
        return
    thread.join(timeout=5)


def run_native_live_paper_session(
    strategy_name: str,
    start_bridge: bool,
    start_launcher: bool,
    bridge_spec: ProcessSpec | None,
    launcher_spec: ProcessSpec | None,
    wait_for_bridge_ready=None,
    bridge_timeout_message: str | None = None,
    lock_acquire=None,
    lock_release=None,
    on_lock_conflict=None,
) -> int:
    lock_token = None
    if start_launcher and lock_acquire is not None:
        lock_token = lock_acquire()
        if lock_token is not None:
            if on_lock_conflict is not None:
                return int(on_lock_conflict(lock_token))
            return 1

    try:
        if not start_bridge and not start_launcher:
            return 0

        if start_bridge and not start_launcher:
            if bridge_spec is None:
                raise ValueError(f"{strategy_name} bridge spec is required when start_bridge=True")
            bridge_process, bridge_thread = start_logged_process(bridge_spec)
            try:
                return bridge_process.wait()
            except KeyboardInterrupt:
                print(f"[{strategy_name}] interrupted by user; stopping bridge", flush=True)
                return 130
            finally:
                terminate_process(bridge_process)
                join_output_thread(bridge_thread)

        if start_launcher and not start_bridge:
            if launcher_spec is None:
                raise ValueError(f"{strategy_name} launcher spec is required when start_launcher=True")
            launcher_process, launcher_thread = start_logged_process(launcher_spec)
            try:
                return launcher_process.wait()
            except KeyboardInterrupt:
                print(f"[{strategy_name}] interrupted by user; stopping launcher", flush=True)
                return 130
            finally:
                terminate_process(launcher_process)
                join_output_thread(launcher_thread)

        if bridge_spec is None or launcher_spec is None:
            raise ValueError(f"{strategy_name} requires both bridge and launcher specs when both stages are enabled")

        bridge_started_at = time.time()
        bridge_process, bridge_thread = start_logged_process(bridge_spec)
        launcher_process = None
        launcher_thread = None
        try:
            if wait_for_bridge_ready is not None:
                if not wait_for_bridge_ready(bridge_process, bridge_started_at):
                    if bridge_process.poll() is not None:
                        return bridge_process.returncode
                    if bridge_timeout_message:
                        print(bridge_timeout_message, file=sys.stderr, flush=True)
                    return 1

            launcher_process, launcher_thread = start_logged_process(launcher_spec)
            try:
                return launcher_process.wait()
            except KeyboardInterrupt:
                print(f"[{strategy_name}] interrupted by user; stopping launcher and bridge", flush=True)
                return 130
        finally:
            terminate_process(launcher_process)
            terminate_process(bridge_process)
            join_output_thread(launcher_thread)
            join_output_thread(bridge_thread)
    finally:
        if start_launcher and lock_release is not None:
            lock_release()
