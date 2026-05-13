#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import soloquant_orchestrator as orchestrator


STRATEGY_ID = 'AShareSectorSmallCapAlgorithm'

PIPELINE_STAGES = (
    'preprocess_factors',
    'compile',
    'backtest',
    'register',
    'lifecycle',
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_backtest_config() -> Path:
    return repo_root() / 'Launcher' / 'config' / 'config-ashare-sector-smallcap-backtest.json'


def default_live_config() -> Path:
    return repo_root() / 'Launcher' / 'config' / 'config-ashare-sector-smallcap-live-paper.json'


def default_factor_csv() -> Path:
    return repo_root() / 'local_data' / 'ashare-sector-smallcap-factors.csv'


def default_results_dir() -> Path:
    return repo_root() / 'Results' / 'ashare-sector-smallcap' / 'backtest'


def default_registry_path() -> Path:
    return repo_root() / 'Results' / 'soloquant' / 'strategy-registry.json'


def run_preprocess_factors(config: dict) -> dict:
    import subprocess
    script = repo_root() / 'Scripts' / 'ashare_sector_smallcap_factor.py'
    tushare = config.get('tushare-data-path', '/home/project/tushare-downloader/tushare_data')
    output = config.get('factor-output', str(default_factor_csv()))
    start = config.get('start-date', '2019-01-01')
    end = config.get('end-date', '2025-12-31')
    command = [sys.executable, str(script), '--tushare-data', tushare, '--output', output, '--start-date', start, '--end-date', end]
    rc = subprocess.run(command, cwd=str(repo_root()), check=False).returncode
    return {'status': 'ok' if rc == 0 else 'error', 'returncode': rc, 'stage': 'preprocess_factors'}


def run_compile(config: dict) -> dict:
    import subprocess
    dotnet = str(orchestrator.default_config()['lean']['dotnet-binary'])
    command = [dotnet, 'build', 'Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj', '-c', 'Debug']
    rc = subprocess.run(command, cwd=str(repo_root()), check=False).returncode
    return {'status': 'ok' if rc == 0 else 'error', 'returncode': rc, 'stage': 'compile'}


def run_backtest(config: dict) -> dict:
    import subprocess
    config_path = config.get('backtest-config', str(default_backtest_config()))
    command, cwd = orchestrator.build_lean_launcher_command(config_path)
    rc = subprocess.run(command, cwd=str(cwd), check=False).returncode
    summary = {}
    summary_path = default_results_dir() / 'summary.json'
    if summary_path.exists():
        summary = orchestrator.load_json_payload(summary_path)
    return {'status': 'ok' if rc == 0 else 'error', 'returncode': rc, 'stage': 'backtest', 'summary': summary}


def run_register(config: dict) -> dict:
    registry_path = Path(config.get('registry-file', str(default_registry_path())))
    backtest_config = config.get('backtest-config', str(default_backtest_config()))
    live_config = config.get('live-config', str(default_live_config()))

    summary = {}
    summary_path = default_results_dir() / 'summary.json'
    if summary_path.exists():
        summary = orchestrator.load_json_payload(summary_path)

    best_score = orchestrator.safe_float(summary.get('score'), 0.0)
    optimization_result = {
        'best_version': 'baseline',
        'best_score': best_score,
        'evaluated_versions': [{'version': 'baseline', 'score': best_score}],
    }

    orchestrator.update_strategy_registry(
        registry_path,
        STRATEGY_ID,
        optimization_result,
        live_config,
    )

    return {'status': 'ok', 'stage': 'register', 'strategy_id': STRATEGY_ID, 'best_score': best_score}


def run_lifecycle(config: dict) -> dict:
    registry_path = Path(config.get('registry-file', str(default_registry_path())))
    influx_config = config.get('influxdb', {'url': 'http://localhost:8086', 'org': 'lean', 'bucket': 'quant'})

    registry = orchestrator.load_json_payload(registry_path) if registry_path.exists() else {'strategies': []}
    strategies = registry.get('strategies', []) if isinstance(registry.get('strategies'), list) else []
    now = datetime.now(timezone.utc)

    target = None
    for s in strategies:
        if isinstance(s, dict) and s.get('strategy_id') == STRATEGY_ID:
            target = s
            break

    if not target:
        return {'status': 'ok', 'stage': 'lifecycle', 'note': 'strategy_not_registered'}

    best_score = orchestrator.safe_float(target.get('best_score'), None)
    live_score = orchestrator.safe_float(target.get('live_score'), None)

    if live_score is None:
        target['status'] = 'candidate'
    elif best_score is not None and (best_score - live_score) > abs(best_score) * 0.25:
        target['status'] = 'retired'
        target['retired_at_utc'] = now.isoformat()
        target['retire_reason'] = 'live_score_degraded'
    elif best_score is not None and best_score >= 0.0:
        target['status'] = 'serving'
    else:
        target['status'] = 'candidate'

    target['lifecycle_updated_at_utc'] = now.isoformat()
    orchestrator.write_json_payload(registry_path, registry)

    if influx_config:
        try:
            from soloquant_pipeline_runner import export_lifecycle_to_influx
            export_lifecycle_to_influx(registry_path, influx_config, now=now)
        except Exception:
            pass

    return {'status': 'ok', 'stage': 'lifecycle', 'strategy_status': target.get('status')}


STAGE_RUNNERS = {
    'preprocess_factors': run_preprocess_factors,
    'compile': run_compile,
    'backtest': run_backtest,
    'register': run_register,
    'lifecycle': run_lifecycle,
}


def run_pipeline(config: dict, stages: tuple[str, ...] | None = None) -> dict:
    stages = stages or PIPELINE_STAGES
    report = {'status': 'ok', 'stages': [], 'started_at_utc': datetime.now(timezone.utc).isoformat()}
    for stage in stages:
        runner_fn = STAGE_RUNNERS.get(stage)
        if not runner_fn:
            report['stages'].append({'stage': stage, 'status': 'skipped', 'reason': 'unknown_stage'})
            continue
        stage_report = runner_fn(config)
        report['stages'].append(stage_report)
        if stage_report.get('status') != 'ok':
            report['status'] = 'error'
            report['failed_stage'] = stage
            break
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='A-share Sector SmallCap full pipeline')
    parser.add_argument('--backtest-config', default=str(default_backtest_config()))
    parser.add_argument('--live-config', default=str(default_live_config()))
    parser.add_argument('--registry-file', default=str(default_registry_path()))
    parser.add_argument('--tushare-data-path', default='/home/project/tushare-downloader/tushare_data')
    parser.add_argument('--factor-output', default=str(default_factor_csv()))
    parser.add_argument('--start-date', default='2019-01-01')
    parser.add_argument('--end-date', default='2025-12-31')
    parser.add_argument('--influxdb-url', default='http://localhost:8086')
    parser.add_argument('--skip-factor', action='store_true')
    parser.add_argument('--skip-compile', action='store_true')
    parser.add_argument('--skip-backtest', action='store_true')
    parser.add_argument('--only', nargs='*', help='Run only specified stages')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = {
        'backtest-config': args.backtest_config,
        'live-config': args.live_config,
        'registry-file': args.registry_file,
        'tushare-data-path': args.tushare_data_path,
        'factor-output': args.factor_output,
        'start-date': args.start_date,
        'end-date': args.end_date,
        'influxdb': {'url': args.influxdb_url, 'org': 'lean', 'bucket': 'quant', 'token-env-var': 'INFLUXDB_TOKEN'},
    }

    stages = tuple(args.only) if args.only else PIPELINE_STAGES
    if args.skip_factor and 'preprocess_factors' in stages:
        stages = tuple(s for s in stages if s != 'preprocess_factors')
    if args.skip_compile and 'compile' in stages:
        stages = tuple(s for s in stages if s != 'compile')
    if args.skip_backtest and 'backtest' in stages:
        stages = tuple(s for s in stages if s != 'backtest')

    report = run_pipeline(config, stages)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get('status') == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
