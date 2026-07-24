"""rl-vs-composite 真实配对回测对比 (auto-evolution 效果验证).

启动 inference_server (ONNX) → 跑 risk-mode=rl 回测 → 跑 risk-mode=composite 回测 → 对比.
这是 Layer C 部署后的真实效果对比, 不是 FQE 估计.
"""
import json, pathlib, subprocess, os, time, sys
from manifest_loader import load_manifest
from lean_runner import _inject_params_into_config, _resolve_dotnet, _REPO_ROOT, _LAUNCHER_DIR, parse_results

MANIFEST = "Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml"
BASE_CFG = "Launcher/config/config-option-vol-arb-5layer.json"
ONNX = "Results/auto_optimize/option_vol_arb_5layer/cql_policy.onnx"
ENDPOINT = "tcp://127.0.0.1:5556"  # 用 5556 避免与其他进程冲突
STRATEGY = "OptionVolArb5LayerStrategy"


def _run_inference_server_bg():
    """后台启动 inference_server (ONNX + ZMQ REP). 返回 Popen 进程."""
    cmd = ["python3", "inference_server.py",
           "--manifest", MANIFEST, "--onnx", ONNX, "--endpoint", ENDPOINT]
    cwd = str(_REPO_ROOT / "Scripts" / "auto_optimize")
    env = {**os.environ, "PYTHONPATH": cwd}
    proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(1.0)
    return proc


def _run_backtest(params: dict, timeout: int = 900) -> dict:
    """跑一次回测, params 注入 config.parameters. 返回 statistics."""
    cfg = pathlib.Path(BASE_CFG)
    if not cfg.is_absolute():
        cfg = _REPO_ROOT / cfg
    tmp_cfg = _inject_params_into_config(cfg, params)
    dotnet = _resolve_dotnet()
    cmd = [dotnet, "QuantConnect.Lean.Launcher.dll", "--config", str(tmp_cfg)]
    try:
        subprocess.run(cmd, cwd=str(_LAUNCHER_DIR), timeout=timeout,
                       check=True, capture_output=True, text=True)
    except Exception as e:
        return {"_error": str(e)[:300]}
    finally:
        try: tmp_cfg.unlink()
        except OSError: pass
    results_dir = _REPO_ROOT / "Results"
    cands = sorted(results_dir.glob(f"{STRATEGY}-summary.json"), key=lambda p: p.stat().st_mtime)
    if not cands:
        cands = sorted(results_dir.glob(f"{STRATEGY}.json"), key=lambda p: p.stat().st_mtime)
    return parse_results(cands[-1]) if cands else {"_error": "no results"}


def _to_float(val, default=0.0):
    if val is None: return default
    if isinstance(val, (int, float)): return float(val)
    s = str(val).replace("%", "").replace(",", "").strip()
    try: return float(s)
    except ValueError: return default


def main():
    print("=" * 70)
    print("rl-vs-composite 配对回测对比 (option_vol_arb_5layer)")
    print("=" * 70)

    print("\n[1/2] 跑 composite 基线 (risk-mode=composite, 默认 VaR 三级风控)...")
    t0 = time.time()
    comp_stats = _run_backtest({"risk-mode": "composite"})
    print(f"  耗时 {time.time()-t0:.0f}s")

    print("\n[2/2] 跑 rl 模式 (risk-mode=rl, inference_server + ONNX policy)...")
    server = _run_inference_server_bg()
    try:
        t0 = time.time()
        rl_stats = _run_backtest({
            "risk-mode": "rl",
            "rl-server-endpoint": ENDPOINT,
            "rl-fallback-alpha": "0.5",
            "rl-timeout-ms": "200",
        })
        print(f"  耗时 {time.time()-t0:.0f}s")
    finally:
        server.terminate()
        try: server.wait(timeout=5)
        except Exception: server.kill()

    def _row(stats):
        return {
            "Sharpe": _to_float(stats.get("Sharpe Ratio", 0)),
            "Sortino": _to_float(stats.get("Sortino Ratio", 0)),
            "Calmar": _to_float(stats.get("Calmar Ratio", 0)),
            "TotalReturn(%)": _to_float(stats.get("Total Return", 0)),
            "Drawdown(%)": _to_float(stats.get("Drawdown", 0)),
            "TotalOrders": _to_float(stats.get("Total Orders", 0)),
            "WinRate(%)": _to_float(stats.get("Win Rate", 0)),
        }

    comp = _row(comp_stats)
    rl = _row(rl_stats)

    print("\n" + "=" * 70)
    print(f"{'指标':<20} {'composite(基线)':<20} {'rl(ONNX policy)':<20} {'差异':<15}")
    print("-" * 70)
    for k in comp:
        d = rl[k] - comp[k]
        sign = "+" if d >= 0 else ""
        print(f"{k:<20} {comp[k]:<20.4f} {rl[k]:<20.4f} {sign}{d:.4f}")
    print("=" * 70)

    print("\n结论:")
    if rl["Sharpe"] > comp["Sharpe"]:
        print(f"  ✅ rl Sharpe ({rl['Sharpe']:.4f}) > composite ({comp['Sharpe']:.4f}), 提升 {rl['Sharpe']-comp['Sharpe']:.4f}")
    else:
        print(f"  ❌ rl Sharpe ({rl['Sharpe']:.4f}) <= composite ({comp['Sharpe']:.4f}), 下降 {comp['Sharpe']-rl['Sharpe']:.4f}")
    if rl["Drawdown(%)"] < comp["Drawdown(%)"]:
        print(f"  ✅ rl Drawdown ({rl['Drawdown(%)']:.4f}%) < composite ({comp['Drawdown(%)']:.4f}%), 风控更紧")
    else:
        print(f"  ⚠️ rl Drawdown ({rl['Drawdown(%)']:.4f}%) >= composite ({comp['Drawdown(%)']:.4f}%)")

    out = {"composite": comp, "rl": rl,
           "composite_raw": comp_stats, "rl_raw": rl_stats}
    out_path = _REPO_ROOT / "Results" / "auto_optimize" / "option_vol_arb_5layer" / "rl_vs_composite.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n结果已存: {out_path}")


if __name__ == "__main__":
    main()
