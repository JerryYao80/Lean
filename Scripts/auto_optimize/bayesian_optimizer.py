"""Layer A main driver. Spec §5.1. 策略无关, 全从 manifest 读.

auto-update5.md 修复:
- 每 trial 的优化目标改为平稳的原始 Sharpe (compute_stationary_reward),
  不再把随 N 变化的 DSR 当实时 reward (违反贝叶斯优化平稳性假设).
- DSR 只在整轮搜索结束后, 用总 trial 数 N 对 champion trial 做一次性
  事后多重检验校正 (compute_dsr_gate), 作为上线门禁.
- 支持 sqlite 持久化 (--storage), 供 dsrdiag.py 经验分析.
"""
import json, pathlib, sys, argparse
import optuna
import yaml
from manifest_loader import load_manifest
from lean_runner import run_backtest, _REPO_ROOT
from reward import compute_stationary_reward, compute_dsr_gate
from ridge_monitor import check_convergence


def optimize(manifest_path: str, config: dict, n_trials: int = 200,
             storage: str = None, study_name: str = None):
    manifest = load_manifest(manifest_path)
    trial_history = []
    # 记录每 trial 的原始 Sharpe + stats, 供事后 DSR 门禁
    all_stats = []

    def objective(trial):
        params = {}
        for p in manifest.parameter_space:
            if p.type == "int":
                params[p.name] = trial.suggest_int(p.name, int(p.range[0]), int(p.range[1]))
            elif p.log:
                params[p.name] = trial.suggest_float(p.name, float(p.range[0]), float(p.range[1]), log=True)
            else:
                params[p.name] = trial.suggest_float(p.name, float(p.range[0]), float(p.range[1]))
        stats = run_backtest(manifest, params, manifest.lean_config, config["lean"]["timeout_seconds"])
        # 平稳目标: 原始 Sharpe (仅依赖 θ, 不依赖 trial 计数)
        reward = compute_stationary_reward(stats)
        # 存原始 Sharpe 供事后 DSR 门禁 + dsrdiag 排名一致性检验
        trial.set_user_attr("raw_sharpe", reward["sharpe"])
        trial.set_user_attr("sortino", reward.get("sortino", 0))
        trial.set_user_attr("calmar", reward.get("calmar", 0))
        trial.set_user_attr("no_trades", reward.get("no_trades", False))
        if reward.get("no_trades"):
            return float("-inf")
        all_stats.append((trial.number, stats, params))
        trial_history.append(params)
        # 返回平稳 Sharpe 给 sampler
        return reward["objective"]

    # sqlite 持久化 (供 dsrdiag.py 经验分析)
    if storage:
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(),
            storage=storage,
            study_name=study_name or manifest.strategy_name,
            load_if_exists=True,
        )
    else:
        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler())
    study.optimize(objective, n_trials=n_trials)

    ridge_converged = check_convergence(trial_history)

    # 事后 DSR 门禁: 用总 trial 数 N 对 champion trial 做一次性校正
    champion_trial_number = study.best_trial.number if study.best_trial else None
    champion_stats = None
    champion_params = None
    dsr_gate = None
    if all_stats:
        champion_stats = next((s for tn, s, p in all_stats if tn == champion_trial_number), None)
        champion_params = study.best_params
        if champion_stats is not None:
            # baseline_sharpe = 所有 trial 的原始 Sharpe 均值 (稳健基线)
            from reward import _to_float
            sharpes = [_to_float(s.get("Sharpe Ratio", 0)) for _, s, _ in all_stats]
            baseline = sum(sharpes) / len(sharpes)
            dsr_gate = compute_dsr_gate(
                champion_stats, n_trials_total=n_trials,
                baseline_sharpe=baseline, gate_threshold=0.0)

    return {
        "best_params": champion_params,
        "best_value": study.best_value,
        "best_trial_number": champion_trial_number,
        "n_trials": n_trials,
        "ridge_converged": ridge_converged,
        "dsr_gate": dsr_gate,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--n-trials", type=int, default=200)
    ap.add_argument("--storage", default=None, help="sqlite:///path.db (供 dsrdiag 经验分析)")
    ap.add_argument("--study-name", default=None)
    args = ap.parse_args()
    config = yaml.safe_load((_REPO_ROOT / "Scripts" / "auto_optimize" / "config.yaml").read_text())
    result = optimize(args.manifest, config, args.n_trials,
                      storage=args.storage, study_name=args.study_name)
    print(json.dumps(result, indent=2, default=str))

