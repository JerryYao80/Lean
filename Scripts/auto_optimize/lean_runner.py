"""Lean subprocess driver. Spec §5.1-5.2, §5.6."""
import json, subprocess, pathlib
from typing import Dict

def run_backtest(manifest, params: dict, config_path: str, timeout: int = 600) -> Dict:
    """启动 Lean 子进程, 注入 --parameters, 返回 statistics."""
    params_json = json.dumps(params)
    cmd = ["dotnet", "QuantConnect.Lean.Launcher.dll",
           "--config", str(config_path),
           "--parameters", params_json]
    try:
        subprocess.run(cmd, timeout=timeout, check=True, capture_output=True, text=True)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        return {"_error": str(e), "Total Orders": 0, "Sharpe Ratio": 0}
    # 找 results 文件
    results_dir = pathlib.Path("Results")
    results_files = sorted(results_dir.glob(f"{manifest.strategy_name}*.json"), key=lambda p: p.stat().st_mtime)
    if not results_files:
        return {"_error": "no results file", "Total Orders": 0, "Sharpe Ratio": 0}
    return parse_results(results_files[-1])

def parse_results(path) -> Dict:
    try:
        data = json.loads(pathlib.Path(path).read_text())
        return data.get("Statistics", {})
    except Exception:
        return {}
