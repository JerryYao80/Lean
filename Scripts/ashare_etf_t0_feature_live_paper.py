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

import live_paper_runner as runner


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_live_config_path() -> Path:
    return repo_root() / 'Launcher' / 'config' / 'config-ashare-etf-t0-feature-live-paper.json'


def launcher_binary_path() -> Path:
    return repo_root() / 'Launcher' / 'bin' / 'Debug' / 'QuantConnect.Lean.Launcher'


def resolve_live_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_live_config_path().resolve()


def safe_int(value, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


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
        'shared-live-market-snapshot-file': resolve_path(parameters.get('shared-live-market-snapshot-file'), root / 'Results' / 'shared-live-market' / 'ashare-live-price-snapshot.json'),
        'shared-live-market-report-file': resolve_path(parameters.get('shared-live-market-report-file'), root / 'Results' / 'shared-live-market' / 'ashare-live-market-report.json'),
        'shared-live-market-archive-path': resolve_path(parameters.get('shared-live-market-archive-path'), root / 'Data' / 'archive' / 'ashare-live-market-daily-quotes'),
        'shared-live-market-refresh-interval-seconds': max(1, safe_int(parameters.get('shared-live-market-refresh-interval-seconds'), 60)),
        'live-price-source-mode': str(parameters.get('live-price-source-mode') or 'auto'),
        'tushare-data-path': str(parameters.get('tushare-data-path') or '/home/project/tushare-downloader/tushare_data_v2'),
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


def print_live_plan(config_path: Path, runtime_config: dict) -> None:
    print('=' * 100)
    print('A-Share ETF T0 Live Paper')
    print('=' * 100)
    print(f'Config file         : {config_path}', flush=True)
    print('Market data         : Tushare realtime in-session; GBM-simulated daily bars off-session', flush=True)
    print(f'Tushare data path   : {runtime_config["tushare-data-path"]}', flush=True)
    print(f'Live feature path   : {runtime_config["feature-data-path"]}', flush=True)
    print(f'Bridge report       : {runtime_config["live-feature-report-file"]}', flush=True)
    print(f'Shared mkt snapshot : {runtime_config["shared-live-market-snapshot-file"]}', flush=True)
    print(f'Shared mkt report   : {runtime_config["shared-live-market-report-file"]}', flush=True)
    print(f'Shared mkt archive  : {runtime_config["shared-live-market-archive-path"]}', flush=True)
    print(f'Market cache refresh: {runtime_config["shared-live-market-refresh-interval-seconds"]} seconds', flush=True)
    print(f'Price mode          : {runtime_config["live-price-source-mode"]}', flush=True)
    print('Process flow        : full-market rt_k/rt_etf_k snapshot -> T0 ETF universe filter -> feature refresh -> LEAN live-paper', flush=True)
    print('=' * 100)


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
    launcher_dir = launcher_binary_path().resolve().parent
    launcher_dll = launcher_dir / 'QuantConnect.Lean.Launcher.dll'
    command = [runner.dotnet_binary_path(), str(launcher_dll), '--config', str(config_path)]
    return command, launcher_dir


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
    resolved_config_path = resolve_live_config_path(config_path)
    runtime_config = load_live_paper_runtime_config(resolved_config_path)
    print_live_plan(resolved_config_path, runtime_config)
    bridge_spec = runner.ProcessSpec(
        command=build_bridge_command(resolved_config_path, python_executable),
        cwd=repo_root(),
        label='bridge',
        passthrough=False,
    )
    launcher_command, workdir = build_launcher_command(resolved_config_path)
    launcher_spec = runner.ProcessSpec(
        command=launcher_command,
        cwd=workdir,
        label='lean',
        passthrough=True,
    )

    print('[stage 1/3] waiting for live bridge readiness', flush=True)
    print('[stage 2/3] starting standard LEAN live-paper launcher', flush=True)
    print('[stage 3/3] LEAN live-paper running', flush=True)
    return runner.run_native_live_paper_session(
        strategy_name='A-share ETF T0 live paper',
        start_bridge=start_bridge,
        start_launcher=start_launcher,
        bridge_spec=bridge_spec,
        launcher_spec=launcher_spec,
        wait_for_bridge_ready=(lambda process, _started_at: wait_for_bridge_ready(resolved_config_path, process)) if start_bridge and start_launcher else None,
        bridge_timeout_message='Timed out waiting for bridge to materialize live feature files.',
    )


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
