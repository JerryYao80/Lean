"""Generational log: log() + read_history(). Spec §2.2."""
import json
from datetime import datetime, timezone
from pathlib import Path

def _gen_dir(strategy, repo_root="."):
    return Path(repo_root) / "Results" / "auto_optimize" / strategy

def log(strategy, generation, layer_gaps, shaping_overrides, review_status, repo_root="."):
    d = _gen_dir(strategy, repo_root)
    d.mkdir(parents=True, exist_ok=True)
    doc = {"strategy": strategy, "generation": generation,
           "timestamp": datetime.now(timezone.utc).isoformat(),
           "review_status": review_status, "layer_gaps": layer_gaps,
           "shaping_overrides": shaping_overrides}
    (d / f"generation_{generation}.json").write_text(json.dumps(doc, indent=2, default=str))

def read_history(strategy, n, repo_root="."):
    d = _gen_dir(strategy, repo_root)
    if not d.exists():
        return []
    files = sorted(d.glob("generation_*.json"),
                   key=lambda p: int(p.stem.split("_")[1]), reverse=True)
    out = []
    for f in files[:n]:
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            continue
    return out
