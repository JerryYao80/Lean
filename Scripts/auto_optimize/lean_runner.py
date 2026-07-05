"""Lean subprocess driver. Spec §5.1-5.2, §5.6."""
import json, subprocess, pathlib
from typing import Dict

# Repo root = parent of Scripts/auto_optimize/
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_LAUNCHER_DIR = _REPO_ROOT / "Launcher" / "bin" / "Debug"


def run_backtest(manifest, params: dict, config_path: str, timeout: int = 600) -> Dict:
    """启动 Lean 子进程, 注入 --parameters, 返回 statistics.

    Lean Launcher 必须从 Launcher/bin/Debug 启动 (其 config 内部相对路径
    data-folder='../../../Data' 等均以此为 CWD). manifest.lean_config 是相对
    repo root 的路径, 这里转成绝对路径传给 --config.
    """
    params_json = json.dumps(params)
    cfg = pathlib.Path(config_path)
    if not cfg.is_absolute():
        cfg = _REPO_ROOT / cfg
    cmd = ["dotnet", "QuantConnect.Lean.Launcher.dll",
           "--config", str(cfg),
           "--parameters", params_json]
    try:
        subprocess.run(cmd, cwd=str(_LAUNCHER_DIR), timeout=timeout,
                       check=True, capture_output=True, text=True)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        err = getattr(e, "stderr", "") or getattr(e, "output", "") or str(e)
        return {"_error": str(e)[:500], "_stderr": (err or "")[-500:], "Total Orders": 0, "Sharpe Ratio": 0}
    # results-destination-folder 在 config 里是 ../../../Results (相对 Launcher/bin/Debug) = repo root/Results
    results_dir = _REPO_ROOT / "Results"
    results_files = sorted(results_dir.glob(f"{manifest.strategy_name}*.json"),
                           key=lambda p: p.stat().st_mtime)
    if not results_files:
        return {"_error": "no results file", "Total Orders": 0, "Sharpe Ratio": 0}
    return parse_results(results_files[-1])


def parse_results(path) -> Dict:
    try:
        data = json.loads(pathlib.Path(path).read_text())
        return data.get("Statistics", {})
    except Exception:
        return {}

