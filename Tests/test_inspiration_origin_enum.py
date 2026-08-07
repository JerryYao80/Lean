"""Tests for origin enum review_inspiration. Spec §3.5."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts"))
from soloquant_orchestrator import normalize_origin  # noqa: E402


def test_review_inspiration_origin():
    assert normalize_origin("review_inspiration") == "review_inspiration"


def test_existing_origins_unchanged():
    assert normalize_origin("local") == "local"
    assert normalize_origin("cli") == "cli"
    assert normalize_origin("arxiv q-fin") == "web"
    assert normalize_origin(None) == "web"
