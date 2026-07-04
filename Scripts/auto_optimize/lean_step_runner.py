"""Layer C state trace exporter. Spec §6.1. 跑回测让 Lean 写逐 bar 状态."""
import json, subprocess, pathlib
from manifest_loader import load_manifest

def export_state_trace(manifest_path: str, config_path: str, output_path: str):
    """运行 Lean 回测, 策略在 OnData 中通过 IRlStateExportable.SerializeRlState 写 state_trace.jsonl.
    需策略本身在 OnData 中调用 SerializeRlState 并写文件 (首期: 策略侧加 tracing 模式)."""
    manifest = load_manifest(manifest_path)
    # 策略侧通过 env RL_TRACE_PATH 写 trace; Lean 原生不支持, 由策略 Initialize 读 env
    cmd = ["dotnet", "QuantConnect.Lean.Launcher.dll", "--config", str(config_path)]
    env = {"RL_TRACE_PATH": str(output_path), "RL_TRACE_STRATEGY": manifest.strategy_name}
    subprocess.run(cmd, check=True, env={**__import__('os').environ, **env})
    return pathlib.Path(output_path)
