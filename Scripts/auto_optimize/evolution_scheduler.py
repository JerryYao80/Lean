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


def _write_feedback_action(action, path):
    """Spec §4.1: write FeedbackAction to <state_path>.feedback.json for fire_optimization."""
    import json as _json
    from dataclasses import asdict
    try:
        pathlib.Path(path).write_text(_json.dumps(asdict(action), default=str))
    except Exception as ex:
        print(f"  [review_drift] write feedback action failed: {ex}")


def _load_feedback_action(path):
    """Spec §4.2: load FeedbackAction written by check_triggers."""
    import json as _json
    p = pathlib.Path(path)
    if not p.exists():
        return None
    try:
        d = _json.loads(p.read_text())
        import sys as _sys
        _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "feedback"))
        from adapters.base import FeedbackAction
        return FeedbackAction(**d)
    except Exception:
        return None


def check_triggers(config: dict, state: dict, metrics_path: str,
                   results_dir: str = None, manifest=None) -> dict:
    """检查 5 个触发器 (review_drift 新增, spec §4.1), 返回 {trigger_name: should_fire}."""
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

    # 5. review_drift (复盘反馈触发器, spec §4.1) — orchestrate(manifest, results_dir)
    triggers["review_drift"] = False
    if results_dir and manifest:
        try:
            import sys as _sys
            _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "feedback"))
            from signals import orchestrate as orchestrate_feedback
            action = orchestrate_feedback(manifest, results_dir)
            if action is not None:
                triggers["review_drift"] = action.trigger
                if action.trigger:
                    state_path = state.get("_state_path",
                        str(_REPO_ROOT / "Results" / "auto_optimize" / "evolution_state.json"))
                    _write_feedback_action(action, state_path + ".feedback.json")
        except Exception as ex:
            print(f"  [review_drift] orchestrate failed: {ex}")

    # 6. inspiration (启发新策略触发器, spec §4.2)
    triggers["inspiration"] = False
    if results_dir and manifest:
        try:
            import sys as _sys
            _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "inspiration"))
            from trigger import detect as detect_inspiration
            from layer_state import load_layer_states, transition, save_layer_states
            from generations import read_history
            _insp_cfg = (manifest.raw if manifest else {}).get("inspiration", {})
            if _insp_cfg:
                _strat = manifest.strategy_name
                _sp = state.get("_state_path", "")
                _states = load_layer_states(_sp, _strat)
                _min_gens = _insp_cfg.get("persistence", {}).get("min_generations", 3)
                # results_dir may be repo_root (test) or .../Results (production);
                # read_history expects repo_root (parent of Results/).
                _rd_path = pathlib.Path(results_dir)
                _history_root = str(_rd_path.parent) if _rd_path.name == "Results" else str(_rd_path)
                _history = read_history(_strat, _min_gens, repo_root=_history_root)
                _to_inspire = detect_inspiration(_strat, _history, _states,
                    _insp_cfg.get("persistence", {}), ceiling=3.0)
                for _layer in _to_inspire:
                    transition(_states, _layer, "inspiration_pending",
                               pending_since_generation=state.get("generation_count", 0))
                    print(f"  [inspiration] layer '{_layer}' → inspiration_pending")
                # timeout回收
                _timeout = _insp_cfg.get("timeout_generations", 5)
                _gen_now = state.get("generation_count", 0)
                for _layer, _ls in list(_states.items()):
                    if _ls.status == "inspiration_pending":
                        if _gen_now - _ls.pending_since_generation >= _timeout:
                            transition(_states, _layer, "optimizing")
                            print(f"  [inspiration] layer '{_layer}' timeout, 打回 optimizing")
                save_layer_states(_sp, _strat, _states)
                if _to_inspire:
                    triggers["inspiration"] = True
                    state["inspired_layers"] = _to_inspire
        except Exception as ex:
            print(f"  [inspiration] detect failed: {ex}")

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

    # Spec §5.3.1: deploy_gate manual checklist (non-blocking, pure print)
    try:
        import sys as _sys
        _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "feedback"))
        from manifest_loader import load_manifest as _lm_dep
        _m_dep = _lm_dep(manifest_path)
        fb_action = _load_feedback_action(state_path + ".feedback.json")
        if fb_action:
            prev_gen_path = pathlib.Path(state_path + ".feedback.json.prev")
            prev_shaping = {}
            if prev_gen_path.exists():
                import json as _json
                prev_shaping = _json.loads(prev_gen_path.read_text()).get("shaping_overrides", {})
            _print_deploy_checklist(fb_action, onnx_path=policy_pt.replace(".pt", ".onnx"),
                                    manifest_path=manifest_path, prev_gen_shaping=prev_shaping,
                                    state_path=state_path, manifest_strategy_name=_m_dep.strategy_name)
    except Exception as ex:
        print(f"  [deploy_checklist] non-blocking failure: {ex}")

    # Spec §4.1: 代际日志
    try:
        import sys as _sys
        _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "inspiration"))
        from generations import log as log_generation
        from manifest_loader import load_manifest
        _m = load_manifest(manifest_path)
        _fb_action = _load_feedback_action(state_path + ".feedback.json")
        _shaping = _fb_action.shaping_overrides if _fb_action else {}
        _layer_gaps = {}
        _review_status = "unknown"
        # review.json 路径:优先 manifest 声明的 results 子目录,回退 strategy_name (spirit3 #3: 无 gold2 硬编码)
        _review_subdir = _m.raw.get("review", {}).get("results_subdir") or _m.strategy_name
        _review_dir = _REPO_ROOT / "Results" / _review_subdir / "review"
        # review_status 从 sidecar last_review 读真实状态(fail/warn/pass),回退 "unknown" (code-review fix)
        _sidecar_path = _review_dir / "review.last_review.json"
        if _sidecar_path.exists():
            try:
                _sidecar = json.loads(_sidecar_path.read_text())
                _review_status = _sidecar.get("review_status", "unknown")
            except Exception:
                _review_status = "unknown"
        _rj_path = _review_dir / "review.json"
        if _rj_path.exists():
            _rj = json.loads(_rj_path.read_text())
            if _review_status == "unknown":
                _review_status = "pass"
            for _layer, _agg in _rj.get("layer_attribution", {}).items():
                _layer_gaps[_layer] = {"pnl_pct_of_total": _agg.get("pnl_pct_of_total", 0),
                                        "gap": abs(_agg.get("pnl_pct_of_total", 0))}
        _gen_n = state.get("generation_count", 0) + 1
        state["generation_count"] = _gen_n
        log_generation(_m.strategy_name, _gen_n, _layer_gaps, _shaping, _review_status, repo_root=str(_REPO_ROOT))
    except Exception as ex:
        print(f"  [generations] non-blocking failure: {ex}")

    # Spec §4.4: inspiration hypothesize
    _inspired = state.get("inspired_layers", [])
    if _inspired:
        try:
            import sys as _sys
            _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "inspiration"))
            from hypothesize import run as run_hypothesize
            from generations import read_history
            from manifest_loader import load_manifest
            _m = load_manifest(manifest_path)
            _insp_cfg = _m.raw.get("inspiration", {})
            _min_gens = _insp_cfg.get("persistence", {}).get("min_generations", 3)
            _review_doc = {}
            # spirit3 #3: 无 gold2 硬编码,优先 manifest results_subdir
            _review_subdir = _m.raw.get("review", {}).get("results_subdir") or _m.strategy_name
            _rj_path = _REPO_ROOT / "Results" / _review_subdir / "review" / "review.json"
            if _rj_path.exists():
                _review_doc = json.loads(_rj_path.read_text())
            _history = read_history(_m.strategy_name, _min_gens, repo_root=str(_REPO_ROOT))
            for _layer in _inspired:
                try:
                    _md_path = run_hypothesize(_m.strategy_name, _layer, _review_doc, _history,
                                               _m.raw, _insp_cfg.get("llm", {}))
                    print(f"  [inspiration] wrote hypothesis: {_md_path}")
                except Exception as ex:
                    print(f"  [inspiration] hypothesize layer '{_layer}' failed: {ex}")
            state["inspired_layers"] = []
        except Exception as ex:
            print(f"  [inspiration] non-blocking failure: {ex}")
    return True


def _print_deploy_checklist(action, onnx_path, manifest_path, prev_gen_shaping=None,
                             state_path=None, manifest_strategy_name=None):
    """Spec §5.3.1 & §4.5.1: print deploy_gate manual checklist (pure print, non-blocking).
    3 items: (1) out-of-attribution-window comparison, (2) shaping weight delta,
    (3) ONNX obs_dim check. §4.5.1: inspiration 候选策略待审提醒 (spirit2 #3 / spirit3 #1)."""
    prev = prev_gen_shaping or {}
    lines = ["[deploy_gate checklist] (spec §5.3.1 — manual review before deploy)"]
    lines.append("1. 归因窗口外近期数据对比 (out-of-attribution-window):")
    lines.append("   手动跑 challenger vs champion FQE + 1-day 回测 on 近期数据 (非 state_trace 期):")
    lines.append(f"     python3 ope_evaluator.py --policy {onnx_path} --observations <recent>...")
    lines.append(f"     dotnet run --project Launcher --config config-<strategy>-challenger-recent.json")
    lines.append("   确认 challenger 在归因窗口外不劣化 (防 reward 过拟合单次路径).")
    lines.append("2. 代际 shaping 权重核对 (generation_<N>_shaping.json):")
    for term, w in action.shaping_overrides.items():
        prev_w = prev.get(term)
        if prev_w is not None and prev_w != 0 and abs(w / prev_w) > 2.0:
            lines.append(f"   ⚠ {term}: {prev_w}→{w} jump >2x — 确认 review gap 真实 vs 噪声")
        else:
            lines.append(f"   {term}: {w} (prev: {prev_w})")
    lines.append("3. ONNX obs_dim 校验:")
    lines.append(f"   challenger ONNX obs_dim == manifest feedback.observation_fields 长度 ({len(action.observation_fields)})")
    lines.append(f"   manifest: {manifest_path}")
    # Spec §4.5.1: inspiration 候选策略待审提醒 (spirit2 #3 / spirit3 #1)
    if state_path and manifest_strategy_name:
        try:
            import sys as _sys
            _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "inspiration"))
            from layer_state import load_layer_states
            _states = load_layer_states(state_path, manifest_strategy_name)
            for _layer, _ls in _states.items():
                if _ls.status == "redesigned" and _ls.candidate_status == "pending_review":
                    lines.append(f"⚠ inspiration 候选策略待审: layer={_layer}, "
                                 f"inspired_strategy_id={_ls.inspired_strategy_id}, "
                                 f"请去 create-strategy pipeline 产物审/部署/拒绝 (回填 candidate_status)")
        except Exception:
            pass
    out = "\n".join(lines)
    print(out)
    return out


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
        state["_state_path"] = args.state_path
        from manifest_loader import load_manifest
        manifest = load_manifest(args.manifest)
        triggers = check_triggers(config, state, args.metrics_path,
                                  results_dir=str(_REPO_ROOT / "Results"), manifest=manifest)
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
