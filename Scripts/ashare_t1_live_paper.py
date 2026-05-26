#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import live_paper_runner as runner


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_live_config_path() -> Path:
    return repo_root() / 'Launcher' / 'config' / 'config-ashare-t1-live-paper.json'


def launcher_binary_path() -> Path:
    return repo_root() / 'Launcher' / 'bin' / 'Debug' / 'QuantConnect.Lean.Launcher'


def resolve_live_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_live_config_path().resolve()


def build_launcher_command(config_path: str | Path | None = None) -> tuple[list[str], Path]:
    config_path = resolve_live_config_path(config_path)
    launcher_dir = launcher_binary_path().resolve().parent
    launcher_dll = launcher_dir / 'QuantConnect.Lean.Launcher.dll'
    command = [runner.dotnet_binary_path(), str(launcher_dll), '--config', str(config_path)]
    return command, launcher_dir


def build_tui_command(config_path: str | Path | None = None, python_executable: str | None = None) -> list[str]:
    config_path = resolve_live_config_path(config_path)
    executable = python_executable or sys.executable
    return [executable, str(repo_root() / 'Scripts' / 'ashare_t1_tui.py'), str(config_path)]


def terminate_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_live_paper(config_path: str | Path | None = None, run_tui: bool = False, python_executable: str | None = None) -> int:
    launcher_command, workdir = build_launcher_command(config_path)
    launcher_spec = runner.ProcessSpec(
        command=launcher_command,
        cwd=workdir,
        label='lean',
        passthrough=True,
    )
    if not run_tui:
        return runner.run_native_live_paper_session(
            strategy_name='A-share T1 live paper',
            start_bridge=False,
            start_launcher=True,
            bridge_spec=None,
            launcher_spec=launcher_spec,
        )

    launcher_process = subprocess.Popen(launcher_command, cwd=workdir)
    try:
        tui_command = build_tui_command(config_path, python_executable)
        tui_result = subprocess.run(tui_command, cwd=repo_root())
        return tui_result.returncode
    finally:
        terminate_process(launcher_process)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Launch A-share T+1 live-paper engine and optional TUI')
    parser.add_argument('--config', default=str(default_live_config_path()))
    parser.add_argument('--launcher-only', action='store_true')
    parser.add_argument('--tui', action='store_true')
    parser.add_argument('--python-executable')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_tui = bool(args.tui and not args.launcher_only)
    return run_live_paper(args.config, run_tui=run_tui, python_executable=args.python_executable)


if __name__ == '__main__':
    raise SystemExit(main())
