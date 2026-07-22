"""Overfitting diagnosis report. Spec §7.5. 双阈值 (绝对 + 衰减)."""
import json

def generate_report(strategy_name: str, n_trials: int, dsr_is: float,
                    dsr_oos_mean: float, dsr_oos_std: float, ridge_converged: bool,
                    abs_min: float = 0.5, ratio_min: float = 0.6) -> dict:
    ratio = dsr_oos_mean / dsr_is if dsr_is > 0 else 0
    abs_ok = dsr_oos_mean >= abs_min
    ratio_ok = ratio >= ratio_min
    if abs_ok and ratio_ok:
        flag = "PASS"
        reason = f"OOS DSR {dsr_oos_mean:.2f} ≥ {abs_min}; ratio {ratio:.2f} ≥ {ratio_min}"
    elif dsr_oos_mean < abs_min:
        flag = "FAIL"
        reason = f"OOS DSR {dsr_oos_mean:.2f} < {abs_min} (absolute floor)"
    else:
        flag = "WARNING"
        reason = f"OOS DSR {dsr_oos_mean:.2f} ok but ratio {ratio:.2f} < {ratio_min} (poor generalization)"
    return {
        "strategy": strategy_name, "n_trials": n_trials,
        "dsr_is": dsr_is, "dsr_oos_cpcv_mean": dsr_oos_mean, "dsr_oos_cpcv_std": dsr_oos_std,
        "overfitting_flag": flag, "overfitting_reason": reason, "ridge_converged": ridge_converged,
    }
