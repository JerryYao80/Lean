"""Optional pipeline stage. Spec §1.5, §5.3. NEVER blocks deploy_gate by default."""
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
RUN_REVIEW = Path(__file__).resolve().parent / "run_review.py"


def should_run(manifest) -> bool:
    """review_schedule != 'manual' and review: block present → run."""
    r = (getattr(manifest, "raw", {}) or {}).get("review", {})
    if not r:
        return False
    return r.get("review_schedule", "manual") != "manual"


def run_if_scheduled(manifest_path: str, results_dir: str, manifest) -> bool:
    """Invoke run_review.py non-blocking. Returns True if ran. Spec §5.3.

    Call chain (spec §1.5): main() → _check_and_fire() → fire_optimization()
    → run_if_scheduled() [hooked at end of fire_optimization after state-file
    write]. _check_and_fire is a private nested closure (not externally
    hookable), so fire_optimization is the ONLY mount point. review is purely
    additive observability; deploy_gate stays 'manual'.
    """
    if not should_run(manifest):
        return False
    try:
        subprocess.run(
            [sys.executable, str(RUN_REVIEW), "--manifest", manifest_path,
             "--results-dir", results_dir, "--write-manifest"],
            cwd=str(_REPO), capture_output=True, text=True, timeout=600,
        )
        return True
    except Exception as e:
        print(f"[review-overlay] non-blocking failure: {e}", file=sys.stderr)
        return False
