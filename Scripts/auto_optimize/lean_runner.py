"""Lean subprocess driver. Spec §5.1-5.2, §5.6.

auto-update6.md 修复: 参数通过临时 config 的 ``parameters`` 字段注入
(LEAN Launcher 的 ``--parameters`` CLI 不支持 JSON, 用 kvp 且易出错;
config 字段是 LEAN 原生读 ``Config.Get("parameters")`` 的稳定路径).
"""
import json, subprocess, pathlib, shutil, tempfile, os
from typing import Dict

# Repo root = parent of Scripts/auto_optimize/
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_LAUNCHER_DIR = _REPO_ROOT / "Launcher" / "bin" / "Debug"


def _resolve_dotnet() -> str:
    """Find dotnet executable: prefer /usr/local/dotnet/dotnet (this env), fallback to PATH."""
    fixed = "/usr/local/dotnet/dotnet"
    if pathlib.Path(fixed).exists():
        return fixed
    found = shutil.which("dotnet")
    return found or "dotnet"  # let subprocess raise the original error


def _inject_params_into_config(base_config_path: pathlib.Path, params: dict) -> pathlib.Path:
    """生成临时 config, 把 params 注入 ``parameters`` 字段 (LEAN 原生读取路径).

    返回临时 config 路径 (位于系统 temp 目录, 不污染原 config).
    """
    base = json.loads(base_config_path.read_text(encoding="utf-8"))
    existing = base.get("parameters", {}) or {}
    # params 值转 str (LEAN GetParameter 返回 string, 策略自己 parse)
    merged = {**existing, **{k: str(v) for k, v in params.items()}}
    base["parameters"] = merged
    # 写到 temp 文件 (保留原 config 不动)
    fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="lean_cfg_")
    os.close(fd)
    pathlib.Path(tmp_path).write_text(json.dumps(base, indent=2, ensure_ascii=False), encoding="utf-8")
    return pathlib.Path(tmp_path)


def run_backtest(manifest, params: dict, config_path: str, timeout: int = 600) -> Dict:
    """启动 Lean 子进程, 通过临时 config 注入 parameters, 返回 statistics.

    Lean Launcher 必须从 Launcher/bin/Debug 启动 (其 config 内部相对路径
    data-folder='../../../Data' 等均以此为 CWD). manifest.lean_config 是相对
    repo root 的路径, 这里转成绝对路径, 注入 params 后生成临时 config.
    """
    cfg = pathlib.Path(config_path)
    if not cfg.is_absolute():
        cfg = _REPO_ROOT / cfg
    tmp_cfg = _inject_params_into_config(cfg, params)
    dotnet = _resolve_dotnet()
    cmd = [dotnet, "QuantConnect.Lean.Launcher.dll",
           "--config", str(tmp_cfg)]
    try:
        subprocess.run(cmd, cwd=str(_LAUNCHER_DIR), timeout=timeout,
                       check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        return {"_error": f"dotnet not found: {e}", "Total Orders": 0, "Sharpe Ratio": 0}
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        err = getattr(e, "stderr", "") or getattr(e, "output", "") or str(e)
        return {"_error": str(e)[:500], "_stderr": (err or "")[-500:], "Total Orders": 0, "Sharpe Ratio": 0}
    finally:
        try:
            tmp_cfg.unlink()
        except OSError:
            pass
    # results-destination-folder 在 config 里是 ../../../Results (相对 Launcher/bin/Debug) = repo root/Results
    # LEAN writes multiple files: {Strategy}.json (full), {Strategy}-summary.json, {Strategy}-order-events.json
    # Prefer the summary file (smaller, has statistics), fall back to full.
    results_dir = _REPO_ROOT / "Results"
    candidates = sorted(results_dir.glob(f"{manifest.strategy_name}-summary.json"),
                        key=lambda p: p.stat().st_mtime)
    if not candidates:
        candidates = sorted(results_dir.glob(f"{manifest.strategy_name}.json"),
                            key=lambda p: p.stat().st_mtime)
    if not candidates:
        return {"_error": "no results file", "Total Orders": 0, "Sharpe Ratio": 0}
    return parse_results(candidates[-1])


def parse_results(path) -> Dict:
    try:
        data = json.loads(pathlib.Path(path).read_text())
        # LEAN results.json uses lowercase 'statistics' key
        return data.get("Statistics") or data.get("statistics") or {}
    except Exception:
        return {}



