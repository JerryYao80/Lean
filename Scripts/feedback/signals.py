"""orchestrate(manifest, results_dir): merge review.json + sidecar + state_trace → FeedbackAction. Spec §1.4, §3.2.

Path convention (§1.4): state_trace at Results/<strategy>/state_trace.jsonl (NOT env var).
review.json + sidecar at Results/<strategy>/review/.
"""
import importlib
import json
import sys
from pathlib import Path


def _load_json(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def orchestrate(manifest, results_dir):
    """Merge review.json + sidecar → review_doc, load state_trace by convention,
    dispatch adapter.feedback_signal. Returns FeedbackAction or None."""
    fb = (manifest.raw if manifest else {}).get("feedback", {})
    if not fb:
        return None
    strategy = manifest.strategy_name
    review_dir = Path(results_dir) / strategy / "review"
    review_json = _load_json(review_dir / "review.json")
    sidecar = _load_json(review_dir / "review.last_review.json")
    if not review_json or not sidecar:
        return None
    review_doc = {**review_json, "review_status": sidecar.get("review_status"),
                  "last_review": sidecar.get("last_review")}
    state_trace = []
    st_path = Path(results_dir) / strategy / "state_trace.jsonl"
    if st_path.exists():
        for line in st_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    state_trace.append(json.loads(line))
                except Exception:
                    continue
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    mod = importlib.import_module(fb["adapter_module"])
    adapter = getattr(mod, fb["adapter_class"])()
    return adapter.feedback_signal(manifest, review_doc, state_trace)
