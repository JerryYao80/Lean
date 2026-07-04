"""Layer A main driver. Spec §5.1. 策略无关, 全从 manifest 读."""
import json, pathlib, sys, argparse
import optuna
from manifest_loader import load_manifest
from lean_runner import run_backtest
from reward import compute_reward
from ridge_monitor import check_convergence

def optimize(manifest_path: str, config: dict, n_trials: int = 200):
    manifest = load_manifest(manifest_path)
    baseline_sharpe = 0.0
    trial_history = []

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
        nonlocal baseline_sharpe
        reward = compute_reward(stats, n_trials=trial.number + 1, baseline_sharpe=baseline_sharpe)
        if reward.get("no_trades"):
            return float("-inf")
        baseline_sharpe = (baseline_sharpe * trial.number + reward["sharpe"]) / (trial.number + 1)
        trial_history.append(params)
        return reward["dsr"]

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler())
    study.optimize(objective, n_trials=n_trials)

    ridge_converged = check_convergence(trial_history)
    return {
        "best_params": study.best_params,
        "best_value": study.best_value,
        "n_trials": n_trials,
        "ridge_converged": ridge_converged,
    }

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--n-trials", type=int, default=200)
    args = ap.parse_args()
    config = json.loads((pathlib.Path("Scripts/auto_optimize/config.yaml").read_text()))  # 简化, 实际用 yaml
    result = optimize(args.manifest, config, args.n_trials)
    print(json.dumps(result, indent=2))
