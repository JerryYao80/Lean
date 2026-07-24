#!/usr/bin/env python3
"""
LEAN 参数传播隔离验证脚本
=================================

目的
----
在重跑 30 trials 之前,先用最快的方式回答一个问题:
    "config.json 里的 parameters 字段,改了之后,LEAN backtest
     的结果(Sharpe / 订单数)到底有没有变化?"

这个脚本**完全绕开 Optuna 和 bayesian_optimizer.py**,直接对
LEAN CLI 发起 2-N 次独立的 backtest,每次用差异极大的参数组合
(例如 max-position-weight 分别给 0.1 和 0.9),然后对比输出结果。

这样可以把"参数传播管道是否 work"这一个变量,从"策略在当前窗口
是否敏感"里完全隔离出来,不需要等 30 trials 跑完才发现问题依旧。

判断逻辑
--------
- 如果极端参数组之间结果(订单数/Sharpe/关键统计量)出现明显差异
  -> 传参管道 OK,之前 0.209 恒定是策略在该窗口不敏感的问题,
     可以放心去重跑完整 30 trials 或换窗口/换策略
- 如果结果完全一致(哪怕参数改得很极端)
  -> 传参管道依然有问题,不要急着换窗口/换策略,先 grep 策略代码里
     GetParameter(...) 用的 key 名,和 config.json 里的 key 名逐字核对
     (大小写、连字符 vs 下划线、是否有多余前缀等)

使用方法
--------
1. 准备一个参数组文件 param_sets.json,格式:
   [
     {"label": "low",  "params": {"max-position-weight": 0.1, "var-budget": 0.01}},
     {"label": "high", "params": {"max-position-weight": 0.9, "var-budget": 0.10}}
   ]

2. 运行:
   python lean_param_propagation_check.py \\
       --project-dir /path/to/OptionVolArb5Layer \\
       --param-sets param_sets.json \\
       --lean-cmd "lean backtest"

3. 也可以先只生成 config、不真正跑回测,人工核对 parameters 字段和
   策略源码里的 key 是否一致:
   python lean_param_propagation_check.py \\
       --project-dir /path/to/OptionVolArb5Layer \\
       --param-sets param_sets.json \\
       --dry-run \\
       --strategy-src /path/to/OptionVolArb5Layer/main.py
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def load_config(project_dir: Path) -> dict:
    cfg_path = project_dir / "config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"未找到 config.json: {cfg_path}")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def write_config(project_dir: Path, config: dict):
    cfg_path = project_dir / "config.json"
    cfg_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def backup_config(project_dir: Path) -> Path:
    cfg_path = project_dir / "config.json"
    backup_path = project_dir / "config.json.bak_paramcheck"
    shutil.copy2(cfg_path, backup_path)
    return backup_path


def restore_config(project_dir: Path, backup_path: Path):
    cfg_path = project_dir / "config.json"
    shutil.copy2(backup_path, cfg_path)
    backup_path.unlink()


def merge_parameters(base_config: dict, params: dict) -> dict:
    """把 params 合并进 config 的 parameters 字段(不改动其它字段)。"""
    new_config = json.loads(json.dumps(base_config))  # deep copy
    existing = new_config.get("parameters", {})
    existing = {**existing, **{k: str(v) for k, v in params.items()}}
    new_config["parameters"] = existing
    return new_config


# ---------------------------------------------------------------------------
# 静态核对:策略源码里 GetParameter 用的 key,是否和你打算传的 key 完全一致
# ---------------------------------------------------------------------------

GETPARAM_PATTERNS = [
    r"GetParameter\(\s*[\"']([^\"']+)[\"']",     # C#: GetParameter("key", default)
    r"get_parameter\(\s*[\"']([^\"']+)[\"']",    # Python snake_case 封装
    r"self\.GetParameter\(\s*[\"']([^\"']+)[\"']",
]


def check_key_consistency(strategy_src: Path, intended_keys: list) -> dict:
    if not strategy_src.exists():
        return {"error": f"策略源码文件不存在: {strategy_src}"}

    text = strategy_src.read_text(encoding="utf-8", errors="ignore")
    found_keys = set()
    for pat in GETPARAM_PATTERNS:
        for m in re.finditer(pat, text):
            found_keys.add(m.group(1))

    intended_set = set(intended_keys)
    missing_in_code = intended_set - found_keys       # 你想传但代码里没读
    unused_in_config = found_keys - intended_set      # 代码里读了但你没传

    return {
        "keys_found_in_strategy_code": sorted(found_keys),
        "keys_you_intend_to_pass": sorted(intended_set),
        "MISMATCH_you_pass_but_code_never_reads": sorted(missing_in_code),
        "code_reads_but_you_never_pass": sorted(unused_in_config),
        "verdict": (
            "🚩 发现 key 不匹配——你打算传的参数,策略代码里根本没有用相同的 key 去读取,"
            "这会导致参数被静默忽略"
            if missing_in_code else
            "未发现 key 不匹配问题(仅基于正则匹配,建议仍人工浏览一遍确认)"
        ),
    }


# ---------------------------------------------------------------------------
# 运行 backtest 并解析结果
# ---------------------------------------------------------------------------

def run_backtest(project_dir: Path, lean_cmd: str) -> dict:
    cmd = f"{lean_cmd} {project_dir}"
    print(f"    执行: {cmd}")
    proc = subprocess.run(cmd, shell=True, cwd=str(project_dir.parent),
                           capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        return {"error": f"lean backtest 失败, returncode={proc.returncode}",
                "stderr_tail": proc.stderr[-2000:]}

    # 找 summary/statistics json,兼容大小写 key 和文件命名差异
    results_dirs = sorted((project_dir).glob("backtests/*"), reverse=True)
    if not results_dirs:
        return {"error": "未找到 backtests 输出目录"}
    latest = results_dirs[0]

    summary_files = list(latest.glob("*-summary.json")) or list(latest.glob("*.json"))
    if not summary_files:
        return {"error": f"未在 {latest} 找到结果 json"}

    data = json.loads(summary_files[0].read_text(encoding="utf-8", errors="ignore"))
    stats = data.get("Statistics") or data.get("statistics") or {}

    def _num(v):
        if v is None:
            return None
        s = str(v).replace("%", "").replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return v

    return {
        "result_dir": str(latest),
        "sharpe": _num(stats.get("Sharpe Ratio") or stats.get("sharpe ratio")),
        "total_orders": _num(stats.get("Total Orders") or stats.get("total orders")),
        "drawdown": _num(stats.get("Drawdown") or stats.get("drawdown")),
        "net_profit": _num(stats.get("Net Profit") or stats.get("net profit")),
        "raw_statistics_keys": list(stats.keys())[:20],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-dir", required=True, type=Path, help="LEAN 策略项目目录(含 config.json)")
    ap.add_argument("--param-sets", required=True, type=Path, help="参数组 json 文件")
    ap.add_argument("--lean-cmd", default="lean backtest", help="LEAN CLI 回测命令前缀")
    ap.add_argument("--strategy-src", type=Path, default=None,
                     help="策略主文件路径,用于静态核对 GetParameter key 是否匹配")
    ap.add_argument("--dry-run", action="store_true",
                     help="只生成/打印合并后的 config,不真正跑回测")
    args = ap.parse_args()

    project_dir = args.project_dir.resolve()
    param_sets = json.loads(args.param_sets.read_text(encoding="utf-8"))

    all_intended_keys = sorted({k for ps in param_sets for k in ps["params"].keys()})

    # 0. 静态 key 一致性核对(如果提供了策略源码)
    if args.strategy_src:
        print("=== [0] 静态核对: GetParameter key 是否与你打算传的 key 一致 ===")
        key_check = check_key_consistency(args.strategy_src, all_intended_keys)
        print(json.dumps(key_check, indent=2, ensure_ascii=False))
        print()

    base_config = load_config(project_dir)
    backup_path = backup_config(project_dir)

    results = []
    try:
        for ps in param_sets:
            label = ps["label"]
            params = ps["params"]
            print(f"=== 参数组 [{label}]: {params} ===")

            merged = merge_parameters(base_config, params)

            if args.dry_run:
                print("  --dry-run: 合并后的 parameters 字段如下(未真正运行回测):")
                print(json.dumps(merged.get("parameters", {}), indent=2, ensure_ascii=False))
                results.append({"label": label, "params": params, "dry_run": True})
                continue

            write_config(project_dir, merged)
            res = run_backtest(project_dir, args.lean_cmd)
            res.update({"label": label, "params": params})
            results.append(res)
            print(f"  -> sharpe={res.get('sharpe')} total_orders={res.get('total_orders')}")
            print()
    finally:
        if not args.dry_run:
            restore_config(project_dir, backup_path)
        else:
            backup_path.unlink(missing_ok=True)

    if args.dry_run:
        print("\ndry-run 完成。请人工核对上面各组 parameters 字段的 key 名,")
        print("是否和策略代码里 GetParameter(...) 用的 key 完全一致(含大小写/连字符)。")
        return

    # 汇总对比
    print("\n=== 汇总对比 ===")
    print(f"{'label':<10} {'sharpe':<10} {'total_orders':<14} {'drawdown':<10}")
    for r in results:
        print(f"{r.get('label',''):<10} {str(r.get('sharpe')):<10} "
              f"{str(r.get('total_orders')):<14} {str(r.get('drawdown')):<10}")

    sharpes = [r.get("sharpe") for r in results if isinstance(r.get("sharpe"), (int, float))]
    orders = [r.get("total_orders") for r in results if isinstance(r.get("total_orders"), (int, float))]

    print("\n=== 判断 ===")
    if len(set(sharpes)) <= 1 and len(set(orders)) <= 1 and len(results) > 1:
        print("🚩 所有极端参数组的 sharpe/total_orders 完全一致——")
        print("   传参管道很可能仍未生效,不要急着换窗口/换策略。")
        print("   下一步: 检查 config.json 里实际写入的 parameters 字段是否被 LEAN 正确读取,")
        print("   并核对策略代码里 GetParameter key 名(用 --strategy-src 参数重跑本脚本的静态核对部分)。")
    elif len(results) <= 1:
        print("只有一组结果,无法比较,请至少提供 2 组差异较大的参数。")
    else:
        print("✅ 不同参数组之间出现结果差异——传参管道基本确认生效。")
        print("   可以放心去重跑完整 30 trials,或转向窗口/策略敏感性问题的排查。")


if __name__ == "__main__":
    main()

