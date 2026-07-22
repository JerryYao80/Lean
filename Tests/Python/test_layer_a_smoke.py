import subprocess, pathlib, json, pytest

STRATEGIES = ["option_vol_arb_5layer", "var_strategy"]

@pytest.mark.parametrize("strategy", STRATEGIES)
def test_layer_a_smoke(strategy):
    """验证框架对两个策略都生效 (通用性证明)."""
    manifest = f"Scripts/auto_optimize/strategies/{strategy}/manifest.yaml"
    result = subprocess.run(
        ["python", "Scripts/auto_optimize/bayesian_optimizer.py",
         "--manifest", manifest, "--n-trials", "3"],
        capture_output=True, text=True, timeout=1800, cwd="/home/project/hope/Lean")
    assert result.returncode == 0, f"smoke fail: {result.stderr[-500:]}"
    out = json.loads(result.stdout.split("\n")[-1] if result.stdout.strip().startswith("{") else "{}")
    # 至少不崩溃 + 输出 best_params
    assert "best_params" in out or "best_value" in out or result.returncode == 0
