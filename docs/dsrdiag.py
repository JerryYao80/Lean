#!/usr/bin/env python3
"""
DSR 目标函数非平稳性诊断脚本
=================================

用途
----
核实一个具体问题:Optuna 在优化过程中,每个 trial 实际拿去驱动 GP+EI 采样器的
reward(即 trial.report() / objective 函数 return 的值),是不是把
Deflated Sharpe Ratio 的"多重检验惩罚项"(依赖当前已跑的 trial 数 N)
直接当成了实时目标函数。

如果是,那么同一组参数在 trial 5 时和 trial 150 时算出来的分数会不同
(纯粹因为 N 变了),这违反贝叶斯优化代理模型"目标函数是参数的稳定函数"
的基本假设,会导致采集函数从早期就停止有效探索。

本脚本做两件事:
1. 静态代码扫描 —— 在你的 objective/reward 计算源码里查找,DSR 公式中的
   expected_max(或 SR0 / benchmark Sharpe)项,在计算时是否引用了
   "当前已跑 trial 数"这种运行时可变量(len(study.trials)、trial.number、
   study.trials 计数等),而不是一个固定值或跑完之后才计算的值。
2. 经验数据分析 —— 从 Optuna study 存储里把每个 trial 的 (trial_number,
   value, 以及 user_attrs 里如果存了原始 Sharpe/PSR/baseline/se 等中间量)
   取出来,检查:
   a) value 是否随 trial_number 存在系统性下降趋势(不是因为参数变差,
      而是惩罚项本身在变重)
   b) 如果 user_attrs 里存了 raw_sharpe(未做多重检验校正的原始 Sharpe),
      对比 raw_sharpe 的排名 和 value(DSR)的排名 是否高度一致——
      如果 raw_sharpe 明显更好的 trial,其 DSR value 却因为出现时间晚
      (N 更大)而被打压到不如更早、raw_sharpe 更差的 trial,
      这就是非平稳性正在实际影响采样器决策的直接证据

使用方法
--------
    python dsr_stationarity_diagnostic.py \\
        --storage "sqlite:///path/to/optuna_study.db" \\
        --study-name option_vol_arb_5layer \\
        --objective-src /path/to/your/objective_function.py \\
        [--out report.json]

依赖: optuna, pandas, numpy (pip install optuna pandas numpy --break-system-packages)
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# 1. 静态代码扫描
# ---------------------------------------------------------------------------

# 这些 pattern 命中说明 DSR/expected_max 的计算路径里引用了"运行时可变的
# trial 计数",而不是一个固定值——这是非平稳性的直接源头。
SUSPICIOUS_PATTERNS = [
    r"len\(\s*study\.trials\s*\)",
    r"len\(\s*self\.study\.trials\s*\)",
    r"study\.trials\s*\)\s*",
    r"trial\.number",
    r"n_trials\s*=\s*len\(",
    r"len\(\s*completed_trials\s*\)",
    r"len\(\s*trials\s*\)",
]

# DSR/PSR 相关函数名,用来定位相关代码块(不做硬性要求,只是辅助定位)
DSR_FUNC_HINTS = [
    "deflated_sharpe", "dsr", "expected_max", "probabilistic_sharpe",
    "psr", "benchmark_sharpe", "sr0", "trials_correction",
]


def scan_objective_source(src_path: Path) -> dict:
    if not src_path.exists():
        return {"error": f"文件不存在: {src_path}"}

    text = src_path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    findings = []
    for lineno, line in enumerate(lines, start=1):
        for pat in SUSPICIOUS_PATTERNS:
            if re.search(pat, line):
                # 看看这行附近(前后 8 行)是否出现 DSR/PSR 相关关键词,
                # 用于判断这个"运行时计数"是不是被用在 DSR 公式里,
                # 而不是无关的日志/进度打印
                window = "\n".join(lines[max(0, lineno - 9):lineno + 8]).lower()
                context_is_dsr_related = any(h in window for h in DSR_FUNC_HINTS)
                findings.append({
                    "line": lineno,
                    "code": line.strip(),
                    "matched_pattern": pat,
                    "near_dsr_context": context_is_dsr_related,
                })

    high_risk = [f for f in findings if f["near_dsr_context"]]

    return {
        "file": str(src_path),
        "total_matches": len(findings),
        "high_risk_matches_near_dsr_code": len(high_risk),
        "findings": findings,
        "verdict": (
            "🚩 高风险: 在 DSR/PSR 相关代码块附近发现运行时 trial 计数引用,"
            "reward 很可能是非平稳的"
            if high_risk else
            "未在 DSR 相关代码块附近发现运行时 trial 计数引用"
            "(仍建议人工确认 expected_max/baseline 的赋值来源)"
        ),
    }


# ---------------------------------------------------------------------------
# 2. 经验数据分析:从 Optuna study 里读取逐 trial 记录
# ---------------------------------------------------------------------------

def load_trials(storage: str, study_name: str):
    try:
        import optuna
    except ImportError:
        print("需要先安装 optuna: pip install optuna --break-system-packages", file=sys.stderr)
        raise

    study = optuna.load_study(study_name=study_name, storage=storage)
    rows = []
    for t in study.trials:
        if t.value is None:
            continue
        row = {
            "trial_number": t.number,
            "value": t.value,
            "datetime_start": str(t.datetime_start),
            "state": str(t.state),
        }
        # 常见可能存放原始 Sharpe / DSR 中间量的 user_attrs key,按需扩展
        for key in ["raw_sharpe", "sharpe", "psr", "baseline", "se",
                    "expected_max", "n_trials_used", "sortino", "calmar"]:
            if key in t.user_attrs:
                row[key] = t.user_attrs[key]
        rows.append(row)
    return rows


def analyze_stationarity(rows: list) -> dict:
    if not rows:
        return {"error": "没有可用的 trial 记录(value 均为 None 或列表为空)"}

    trial_numbers = np.array([r["trial_number"] for r in rows])
    values = np.array([r["value"] for r in rows])

    # a) value 是否随 trial_number 存在系统性下降趋势
    # 用 Spearman 相关系数:负相关且显著,说明"越晚出现的 trial 分数系统性更低"
    from scipy import stats as sps
    try:
        spearman_r, spearman_p = sps.spearmanr(trial_numbers, values)
    except Exception:
        spearman_r, spearman_p = None, None

    result = {
        "n_trials_with_value": len(rows),
        "value_range": [float(np.min(values)), float(np.max(values))],
        "value_mean": float(np.mean(values)),
        "value_by_trial_number_spearman_r": spearman_r,
        "value_by_trial_number_spearman_p": spearman_p,
        "spearman_interpretation": (
            "显著负相关(p<0.05 且 r<-0.3): 强烈提示 reward 随 N 增大系统性走低,"
            "与惩罚项内嵌进实时目标函数的假设一致"
            if (spearman_r is not None and spearman_p is not None
                and spearman_p < 0.05 and spearman_r < -0.3)
            else "未观察到强系统性趋势(仅凭此项不能排除问题,请结合静态扫描结果)"
        ),
    }

    # b) 如果存了 raw_sharpe,比较 raw_sharpe 排名 vs DSR value 排名的一致性
    has_raw = all("raw_sharpe" in r or "sharpe" in r for r in rows)
    if has_raw:
        raw_key = "raw_sharpe" if "raw_sharpe" in rows[0] else "sharpe"
        raw_vals = np.array([r[raw_key] for r in rows])
        raw_rank = sps.rankdata(-raw_vals)      # 原始 Sharpe 排名(越好排名越前)
        dsr_rank = sps.rankdata(-values)        # DSR value 排名
        rank_corr, rank_p = sps.spearmanr(raw_rank, dsr_rank)

        # 找出"raw_sharpe 排名前 20%,但 DSR value 排名后 50%"的 trial ——
        # 这些就是被非平稳惩罚项"冤枉"打压下去的候选
        n = len(rows)
        top_raw_idx = set(np.argsort(raw_rank)[: max(1, n // 5)])
        bottom_dsr_idx = set(np.argsort(-dsr_rank)[: max(1, n // 2)])
        suppressed = sorted(top_raw_idx & bottom_dsr_idx)

        result["raw_sharpe_vs_dsr_rank_correlation"] = rank_corr
        result["raw_sharpe_vs_dsr_rank_p"] = rank_p
        result["n_trials_suppressed_by_late_arrival"] = len(suppressed)
        result["suppressed_trial_numbers"] = [rows[i]["trial_number"] for i in suppressed]
        result["suppressed_interpretation"] = (
            f"发现 {len(suppressed)} 个 trial 原始 Sharpe 排名靠前,"
            "但因出现较晚(N 较大导致惩罚项更重)被压到 DSR 排名后半段——"
            "这些参数组合可能被优化器错误地判定为'差',建议人工用固定 N 重新评分复核"
            if suppressed else
            "未发现因出现时间导致排名被压制的 trial"
        )
    else:
        result["raw_sharpe_check"] = (
            "user_attrs 中未存储 raw_sharpe/sharpe 字段,无法做排名一致性检验。"
            "建议后续在 objective 函数里用 trial.set_user_attr('raw_sharpe', ...) "
            "把未校正的原始 Sharpe 也存下来,方便复核。"
        )

    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--storage", required=False, help="Optuna storage URL, 例如 sqlite:///study.db")
    ap.add_argument("--study-name", required=False, help="Optuna study 名称")
    ap.add_argument("--objective-src", required=False, type=Path,
                     help="objective/reward 计算函数所在的源码文件路径,用于静态扫描")
    ap.add_argument("--out", default="dsr_stationarity_report.json", help="报告输出路径")
    args = ap.parse_args()

    report = {}

    if args.objective_src:
        print(f"[1/2] 静态扫描源码: {args.objective_src}")
        report["static_scan"] = scan_objective_source(args.objective_src)
        print("  -> " + report["static_scan"].get("verdict", "扫描出错"))
    else:
        report["static_scan"] = {"skipped": "未提供 --objective-src"}

    if args.storage and args.study_name:
        print(f"[2/2] 读取 Optuna study: {args.study_name} @ {args.storage}")
        rows = load_trials(args.storage, args.study_name)
        report["empirical_analysis"] = analyze_stationarity(rows)
        ea = report["empirical_analysis"]
        if "spearman_interpretation" in ea:
            print("  -> " + ea["spearman_interpretation"])
        if "suppressed_interpretation" in ea:
            print("  -> " + ea["suppressed_interpretation"])
    else:
        report["empirical_analysis"] = {"skipped": "未提供 --storage / --study-name"}

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n完整报告已写入: {args.out}")

    # 总结判断
    static_flag = "🚩" in report["static_scan"].get("verdict", "")
    print("\n=== 总体结论 ===")
    if static_flag:
        print("🚩 静态扫描发现高风险模式:reward 计算路径中引用了运行时 trial 计数。")
        print("   建议:把每个 trial 的优化目标换成原始 Sharpe/Sortino,")
        print("   DSR 只在搜索结束后对 champion trial 做一次性事后校正。")
    else:
        print("静态扫描未发现明显问题,但请结合 empirical_analysis 中的排名一致性检验综合判断。")


if __name__ == "__main__":
    main()

