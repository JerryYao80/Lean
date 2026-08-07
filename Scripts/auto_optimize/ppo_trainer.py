"""Layer C PPO trainer. Spec §6.1, §6.8. stable-baselines3."""
import argparse, pathlib, json
from stable_baselines3 import PPO
from gym_env import RlRiskEnv
from manifest_loader import load_manifest

def train(manifest_path: str, trace_path: str, output_dir: str, total_timesteps: int = 10000):
    manifest = load_manifest(manifest_path)
    shaping = [{"term": t.term, "weight": t.weight} for t in manifest.reward_config.shaping]
    var_budget = next((p.default for p in manifest.parameter_space if p.name == "var-budget"), 0.02)
    max_dd = next((p.default for p in manifest.parameter_space if "drawdown" in p.name), 0.2)
    env = RlRiskEnv(trace_path, shaping, var_budget, max_dd)
    model = PPO("MlpPolicy", env, verbose=1, n_steps=128, batch_size=64, n_epochs=5)
    model.learn(total_timesteps=total_timesteps)
    out = pathlib.Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    model.save(out / "policy.pt")
    return out / "policy.pt"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--timesteps", type=int, default=10000)
    args = ap.parse_args()
    train(args.manifest, args.trace, args.output_dir, args.timesteps)
