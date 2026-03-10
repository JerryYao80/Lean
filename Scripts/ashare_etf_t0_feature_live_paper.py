#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_live_config_path() -> Path:
    return repo_root() / 'Launcher' / 'config' / 'config-ashare-etf-t0-feature-live-paper.json'


def launcher_binary_path() -> Path:
    return repo_root() / 'Launcher' / 'bin' / 'Debug' / 'QuantConnect.Lean.Launcher'


def resolve_live_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_live_config_path().resolve()


def load_live_paper_runtime_config(config_path: str | Path | None = None) -> dict:
    config_file = resolve_live_config_path(config_path)
    loaded = json.loads(config_file.read_text(encoding='utf-8'))
    parameters = loaded.get('parameters') if isinstance(loaded, dict) else None
    if not isinstance(parameters, dict):
        parameters = {}

    launcher_workdir = repo_root() / 'Launcher' / 'bin' / 'Debug'

    def resolve_path(value, default: Path) -> Path:
        path = Path(str(value)) if value else default
        if not path.is_absolute():
            path = (launcher_workdir / path).resolve()
        return path

    root = repo_root()
    return {
        'feature-data-path': resolve_path(parameters.get('feature-data-path'), root / 'Data' / 'alternative' / 'ashare-etf-t0-live-features'),
        'live-feature-report-file': resolve_path(parameters.get('live-feature-report-file'), root / 'Results' / 'ashare-etf-t0-feature-live-bridge-report.json'),
    }


def wait_for_bridge_ready(
    config_path: str | Path | None,
    bridge_process: subprocess.Popen,
    timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 1.0,
) -> bool:
    runtime_config = load_live_paper_runtime_config(config_path)
    feature_data_path = Path(runtime_config['feature-data-path'])
    deadline = time.time() + max(1.0, float(timeout_seconds))

    while time.time() < deadline:
        if bridge_process.poll() is not None:
            return False
        if any(feature_data_path.glob('*/*/*.csv')):
            return True
        time.sleep(max(0.1, float(poll_interval_seconds)))

    return any(feature_data_path.glob('*/*/*.csv'))


def build_bridge_command(config_path: str | Path | None = None, python_executable: str | None = None) -> list[str]:
    executable = python_executable or sys.executable
    return [
        executable,
        str(repo_root() / 'Scripts' / 'ashare_etf_t0_feature_live_bridge.py'),
        '--config',
        str(resolve_live_config_path(config_path)),
    ]


def build_launcher_command(config_path: str | Path | None = None) -> tuple[list[str], Path]:
    config_path = resolve_live_config_path(config_path)
    launcher = launcher_binary_path().resolve()
    command = [str(launcher), '--config', str(config_path)]
    return command, launcher.parent


def terminate_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_live_paper(
    config_path: str | Path | None = None,
    start_bridge: bool = True,
    start_launcher: bool = True,
    python_executable: str | None = None,
) -> int:
    if not start_bridge and not start_launcher:
        return 0

    if start_bridge and not start_launcher:
        return subprocess.run(build_bridge_command(config_path, python_executable), cwd=repo_root()).returncode

    if start_launcher and not start_bridge:
        launcher_command, workdir = build_launcher_command(config_path)
        return subprocess.run(launcher_command, cwd=workdir).returncode

    bridge_process = subprocess.Popen(build_bridge_command(config_path, python_executable), cwd=repo_root())
    launcher_process = None
    try:
        if not wait_for_bridge_ready(config_path, bridge_process):
            if bridge_process.poll() is not None:
                return bridge_process.returncode
            print('Timed out waiting for bridge to materialize live feature files.', file=sys.stderr)
            return 1

        launcher_command, workdir = build_launcher_command(config_path)
        launcher_process = subprocess.Popen(launcher_command, cwd=workdir)
        return launcher_process.wait()
    finally:
        terminate_process(launcher_process)
        terminate_process(bridge_process)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Launch A-share ETF T0 feature live bridge and live-paper engine')
    parser.add_argument('--config', default=str(default_live_config_path()))
    parser.add_argument('--bridge-only', action='store_true')
    parser.add_argument('--launcher-only', action='store_true')
    parser.add_argument('--python-executable')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start_bridge = not args.launcher_only
    start_launcher = not args.bridge_only
    return run_live_paper(
        args.config,
        start_bridge=start_bridge,
        start_launcher=start_launcher,
        python_executable=args.python_executable,
    )


if __name__ == '__main__':
    raise SystemExit(main())
