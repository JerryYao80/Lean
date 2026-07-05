"""200 trials DSR 衰减分析 (auto-update4.md).

解析 bayesian_optimizer.py 的 Optuna 日志, 产出:
1. DSR vs trial_count 曲线 — 惩罚项是否随样本量合理衰减
2. best_params 漂移 — trial 50 vs 200 最优参数是否稳定
3. OOS 衰减估算 — 用 trial 内 Sharpe 反推 IS→OOS 衰减比例
"""
import re, json, pathlib, argparse, sys

TRIAL_RE = re.compile(
    r"Trial (\d+) finished with value: (-?\d+\.?\d*(?:[eE][+-]?\d+)?|-?Infinity|inf)"
    r" and parameters: (\{[^}]*\}\.?)\s+Best is trial (\d+) with value: (.+)\."
)


def parse_log(log_path):
    trials = []
    for line in pathlib.Path(log_path).read_text().splitlines():
        m = TRIAL_RE.search(line)
        if not m:
            continue
        idx = int(m.group(1))
        val_str = m.group(2)
        try:
            val = float(val_str) if val_str not in ("Infinity", "-Infinity") else (
                float("inf") if val_str == "Infinity" else float("-inf"))
        except ValueError:
            val = float("nan")
        params_str = m.group(3).rstrip(".")
        try:
            params = eval(params_str, {"__builtins__": {}}, {})
        except Exception:
            params = {}
        best_idx = int(m.group(4))
        best_val_str = m.group(5).strip()
        try:
            best_val = float(best_val_str) if best_val_str not in ("Infinity", "-Infinity") else (
                float("inf") if best_val_str == "Infinity" else float("-inf"))
        except ValueError:
            best_val = float("nan")
        trials.append({
            "trial": idx, "dsr": val, "params": params,
            "best_trial": best_idx, "best_dsr": best_val
        })
    return trials


def analyze_dsr_decay(trials):
    valid = [t for t in trials if t["dsr"] != float("-inf")]
    n = len(valid)
    if n == 0:
        return {"error": "no valid trials"}

    milestones = [10, 25, 50, 100, 150, 200]
    decay_curve = []
    for m in milestones:
        if m > len(trials):
            break
        slice_ = trials[:m]
        valid_slice = [t for t in slice_ if t["dsr"] != float("-inf")]
        if not valid_slice:
            continue
        best = max(t["dsr"] for t in valid_slice)
        decay_curve.append({
            "trial_count": m,
            "best_dsr": best,
            "valid_trials": len(valid_slice),
            "inf_trials": m - len(valid_slice),
        })

    def best_at(k):
        slice_ = trials[:k]
        valid = [t for t in slice_ if t["dsr"] != float("-inf")]
        if not valid:
            return None
        return max(valid, key=lambda t: t["dsr"])

    drift = {}
    for k in [50, 100, 200]:
        if k <= len(trials):
            b = best_at(k)
            if b:
                drift[f"best_at_trial_{k}"] = {
                    "trial_idx": b["trial"],
                    "dsr": b["dsr"],
                    "params": b["params"],
                }

    stability = {"drift_detected": False}
    b50 = best_at(50)
    b200 = best_at(200) if len(trials) >= 200 else best_at(len(trials))
    if b50 and b200 and b50["trial"] != b200["trial"]:
        for key in ["var-budget", "max-drawdown", "iv-rv-z-score-threshold"]:
            v50 = b50["params"].get(key)
            v200 = b200["params"].get(key)
            if v50 is not None and v200 is not None and v50 > 0:
                rel_change = abs(v200 - v50) / abs(v50)
                if rel_change > 0.3:
                    stability["drift_detected"] = True
                stability[key] = {
                    "at_50": v50, "at_200": v200,
                    "relative_change": rel_change,
                    "drifts": rel_change > 0.3,
                }

    return {
        "n_total_trials": len(trials),
        "n_valid_trials": n,
        "n_inf_trials": len(trials) - n,
        "dsr_decay_curve": decay_curve,
        "best_params_drift": drift,
        "param_stability": stability,
    }


def estimate_oos_decay(trials):
    curve = analyze_dsr_decay(trials).get("dsr_decay_curve", [])
    if len(curve) < 2:
        return {"error": "insufficient milestones"}

    decays = []
    for i in range(1, len(curve)):
        prev = curve[i-1]["best_dsr"]
        cur = curve[i]["best_dsr"]
        if prev > 0:
            decay_ratio = (prev - cur) / prev
            decays.append({
                "from_trial": curve[i-1]["trial_count"],
                "to_trial": curve[i]["trial_count"],
                "from_dsr": prev,
                "to_dsr": cur,
                "decay_ratio": decay_ratio,
            })

    max_decay = max((d["decay_ratio"] for d in decays), default=0)
    final_decay = decays[-1]["decay_ratio"] if decays else 0
    return {
        "decay_steps": decays,
        "max_decay_ratio": max_decay,
        "final_decay_ratio": final_decay,
        "acceptable_threshold_30pct": max_decay < 0.30,
        "verdict": "PASS" if max_decay < 0.30 else "WARNING_OOS_DECAY_AMPLIFIED",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="path to 200trials.log")
    args = ap.parse_args()
    trials = parse_log(args.log)
    if not trials:
        print(json.dumps({"error": "no trials parsed"}))
        sys.exit(1)
    result = {
        "dsr_decay_analysis": analyze_dsr_decay(trials),
        "oos_decay_estimate": estimate_oos_decay(trials),
    }
    print(json.dumps(result, indent=2, default=str))
