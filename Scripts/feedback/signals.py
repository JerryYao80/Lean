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
    dispatch adapter.feedback_signal. Returns FeedbackAction or None.

    Path convention (§1.4): tries results_dir/<strategy_name>/review/ first,
    then results_dir/review/ (when results_dir IS the backtest results folder,
    e.g. Results/gold2-betavol whose name differs from strategy_name).
    state_trace at <candidate_dir>/../state_trace.jsonl."""
    fb = (manifest.raw if manifest else {}).get("feedback", {})
    if not fb:
        return None
    strategy = manifest.strategy_name
    # Try candidate review dirs: results_dir/strategy_name, then results_dir itself
    candidates = [
        Path(results_dir) / strategy,
        Path(results_dir),
    ]
    review_json = sidecar = None
    base_dir = None
    for cand in candidates:
        rj = _load_json(cand / "review" / "review.json")
        sc = _load_json(cand / "review" / "review.last_review.json")
        if rj and sc:
            review_json, sidecar, base_dir = rj, sc, cand
            break
    if not review_json or not sidecar:
        return None
    review_doc = {**review_json, "review_status": sidecar.get("review_status"),
                  "last_review": sidecar.get("last_review")}
    state_trace = []
    st_path = base_dir / "state_trace.jsonl"
    if st_path.exists():
        for line in st_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    state_trace.append(json.loads(line))
                except Exception:
                    continue
    _self_dir = Path(__file__).resolve().parent          # Scripts/feedback
    _scripts_dir = _self_dir.parent                       # Scripts
    sys.path.insert(0, str(_self_dir))                    # makes adapters.<x> importable
    sys.path.insert(0, str(_scripts_dir))                 # makes feedback.adapters.<x> importable
    mod = importlib.import_module(fb["adapter_module"])
    adapter = getattr(mod, fb["adapter_class"])()
    return adapter.feedback_signal(manifest, review_doc, state_trace)
