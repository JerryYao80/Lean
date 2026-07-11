"""Evolution scheduler: 按 config 触发器调用 Layer A + Layer C.

按 docs/self-update-youhua.md 重大纰漏 #3 修复:
触发器全是滞后指标且未接线到调度器 → 写一个真调度器, 按以下条件触发:

1. 数据累积 >= train_window_days (默认 504 天 = 2 年)
2. 实盘 Sharpe 衰减 > 30% vs 回测 (滞后触发)
3. 周期 (weekly, 默认)
4. kill-switch 触发 (立即重训 Layer C)

设计: 一个长跑 daemon, 每 N 小时检查触发条件, 满足则调用 bayesian_optimizer +
offline_rl_trainer + policy_exporter. deploy_gate 仍为 manual (人工审核后部署).

用法:
    python3 evolution_scheduler.py --manifest <manifest.yaml> [--check-interval-hours 6]

注: 这是 daemon, 需配合 supervisor/systemd 常驻. 首期 deploy_gate=manual,
故 scheduler 只做"触发搜索+训练", 部署仍需人工 confirm.
"""
import json, pathlib, time, argparse, subprocess, os, sys
from datetime import datetime
import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "auto_optimize"))


def _load_config():
    return yaml.safe_load((_REPO_ROOT / "Scripts" / "auto_optimize" / "config.yaml").read_text())


def _read_live_metrics(metrics_path: str) -> dict:
    """读最近一次 live-paper 的实盘指标 (Sharpe 等). 由 live-paper bridge 写入."""
    p = pathlib.Path(metrics_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def check_triggers(config: dict, state: dict, metrics_path: str) -> dict:
    """检查 4 个触发器, 返回 {trigger_name: should_fire}."""
    triggers = {}
    ppo_cfg = config.get("ppo_training", {})

    # 1. 数据累积 >= train_window_days
    train_window = ppo_cfg.get("train_window_days", 504)
    last_opt = state.get("last_optimize_date")
    if last_opt:
        days_since_opt = (datetime.utcnow() - datetime.fromisoformat(last_opt)).days
        triggers["data_accumulation"] = days_since_opt >= train_window
    else:
        triggers["data_accumulation"] = True  # 从未优化过

    # 2. 实盘 Sharpe 衰减 > 30%
    live = _read_live_metrics(metrics_path)
    live_sharpe = live.get("live_sharpe")
    backtest_sharpe = state.get("last_backtest_sharpe")
    if live_sharpe is not None and backtest_sharpe and backtest_sharpe > 0:
        decay = (backtest_sharpe - live_sharpe) / backtest_sharpe
        triggers["sharpe_decay"] = decay > 0.30
    else:
        triggers["sharpe_decay"] = False

    # 3. 周期 (weekly)
    if last_opt:
        days_since = (datetime.utcnow() - datetime.fromisoformat(last_opt)).days
        triggers["weekly"] = days_since >= 7
    else:
        triggers["weekly"] = True

    # 4. kill-switch
    triggers["kill_switch"] = live.get("kill_switch_tripped", False)

    return triggers


def fire_optimization(manifest_path: str, config: dict, state_path: str):
    """触发 Layer A (bayesian) + Layer C (offline RL) 全流程."""
    print(f"[{datetime.utcnow():%Y-%m-%d %H:%M}] 触发自动优化...")
    n_trials = config.get("optuna", {}).get("n_trials", 200)
    ao_dir = str(_REPO_ROOT / "Scripts" / "auto_optimize")
    env = {**os.environ, "PYTHONPATH": ao_dir}

    # Layer A: 贝叶斯优化
    print(f"  [Layer A] bayesian_optimizer (n_trials={n_trials})...")
    r = subprocess.run(
        ["python3", "bayesian_optimizer.py", "--manifest", manifest_path,
         "--n-trials", str(n_trials)],
        cwd=ao_dir, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(f"  [Layer A] 失败: {r.stderr[-300:]}")
        return False
    try:
        a_result = json.loads(r.stdout.strip().split("\n")[-1])
        print(f"  [Layer A] best_value={a_result.get('best_value')}, trial={a_result.get('best_trial_number')}")
    except Exception:
        a_result = {}

    # Layer C: 离线 RL
    trace = "Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl"
    policy_pt = "Results/auto_optimize/option_vol_arb_5layer/cql_policy.pt"
    print(f"  [Layer C] offline_rl_trainer (CQL 500 steps)...")
    r2 = subprocess.run(
        ["python3", "offline_rl_trainer.py", "--trace", trace,
         "--output", policy_pt, "--algorithm", "cql", "--n-steps", "500"],
        cwd=ao_dir, capture_output=True, text=True, env=env)
    if r2.returncode != 0:
        print(f"  [Layer C] 失败: {r2.stderr[-300:]}")
        return False

    # 导出 ONNX
    r3 = subprocess.run(
        ["python3", "policy_exporter.py", "--policy", policy_pt,
         "--output", policy_pt.replace(".pt", ".onnx"), "--obs-dim", "8"],
        cwd=ao_dir, capture_output=True, text=True, env=env)
    if r3.returncode != 0:
        print(f"  [ONNX export] 失败: {r3.stderr[-300:]}")
        return False

    # 更新 state
    state = json.loads(pathlib.Path(state_path).read_text()) if pathlib.Path(state_path).exists() else {}
    state["last_optimize_date"] = datetime.utcnow().isoformat()
    state["last_backtest_sharpe"] = a_result.get("best_value")
    state["last_optimize_result"] = a_result
    pathlib.Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(state_path).write_text(json.dumps(state, indent=2, default=str))
    print(f"  ✅ 优化完成, state 已更新: {state_path}")
    print(f"  ⚠️ deploy_gate=manual, 需人工审核后部署 ONNX")

    # Review overlay (spec §1.5, §5.3): weekly cadence, non-blocking.
    # fire_optimization is the ONLY module-level hook point (_check_and_fire at
    # line 146 is a private nested closure, not externally hookable).
    try:
        import sys as _sys
        _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "review"))
        from pipeline_overlay import run_if_scheduled
        from manifest_loader import load_manifest as _lm
        m = _lm(manifest_path)
        results_dir = str(_REPO_ROOT / "Results" / m.strategy_name)
        run_if_scheduled(manifest_path, results_dir, m)
    except Exception as ex:
        print(f"  [review-overlay] non-blocking failure: {ex}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--check-interval-hours", type=float, default=6.0)
    ap.add_argument("--state-path", default="Results/auto_optimize/evolution_state.json")
    ap.add_argument("--metrics-path", default="Results/auto_optimize/live_metrics.json")
    ap.add_argument("--once", action="store_true", help="只检查一次, 不常驻")
    args = ap.parse_args()

    config = _load_config()

    def _check_and_fire():
        state = json.loads(pathlib.Path(args.state_path).read_text()) if pathlib.Path(args.state_path).exists() else {}
        triggers = check_triggers(config, state, args.metrics_path)
        print(f"[{datetime.utcnow():%Y-%m-%d %H:%M}] 触发器状态: {triggers}")
        if any(triggers.values()):
            fired = [k for k, v in triggers.items() if v]
            print(f"  → 触发: {fired}")
            fire_optimization(args.manifest, config, args.state_path)
        else:
            print(f"  → 无触发器满足, 跳过")

    if args.once:
        _check_and_fire()
        return

    print(f"Evolution scheduler 启动, 每 {args.check_interval_hours}h 检查一次 (Ctrl+C 退出)")
    while True:
        try:
            _check_and_fire()
        except Exception as e:
            print(f"  调度异常: {e}")
        time.sleep(args.check_interval_hours * 3600)


if __name__ == "__main__":
    main()
