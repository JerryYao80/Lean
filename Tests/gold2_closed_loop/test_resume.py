"""Failing contracts for the pre-open resume rules (Task 15, Phase 4).

Design invariants (spec §10 resume rules, §5 lifecycle):

* Operation IDs are determined by
  experiment/window/stage/partition/candidate/scenario/attempt.
* Pre-open resume skips ONLY complete, hash-valid terminal operation IDs.
* Incomplete directories are QUARANTINED (not silently reused): a
  partial run dir is moved aside so a re-execution starts clean and cannot
  accidentally consume a stale partial packet.
* After ``BLIND_ACCESS_OPENED`` normal resume is FORBIDDEN: any resume
  attempt raises (post-blind mutation rejection).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from Scripts.gold2_closed_loop.blind_evaluator import (
    BlindEvaluator,
    FrozenCandidate,
    OperationId,
    ResumeDecision,
    classify_resume,
)


def _op(window: str = "W1", stage: str = "G0", candidate: str = "C0",
        attempt: int = 1) -> OperationId:
    return OperationId(
        experiment_id="E1",
        window_id=window,
        stage_id=stage,
        partition=f"{window}/train",
        candidate_id=candidate,
        scenario="blind",
        attempt=attempt,
    )


# --- pre-open resume: skip only complete hash-valid ops ---------------


def test_complete_hash_valid_op_is_skipped(tmp_path):
    op = _op()
    run_dir = tmp_path / "run" / op.path_slug()
    run_dir.mkdir(parents=True)
    (run_dir / "packet.json").write_text('{"done": true}')
    decision = classify_resume(op, run_dir, expected_packet_sha256=_sha(run_dir / "packet.json"))
    assert decision == ResumeDecision.SKIP


def test_incomplete_op_dir_is_quarantined(tmp_path):
    op = _op()
    run_dir = tmp_path / "run" / op.path_slug()
    run_dir.mkdir(parents=True)
    (run_dir / "packet.json").write_text('{"done": true}')
    # Corrupt the packet so its hash no longer matches.
    decision = classify_resume(op, run_dir, expected_packet_sha256="0" * 64)
    assert decision == ResumeDecision.QUARANTINE
    # The partial dir was moved aside (quarantined), not left in place.
    assert not (run_dir / "packet.json").exists()


def test_missing_op_dir_is_run(tmp_path):
    op = _op()
    run_dir = tmp_path / "run" / op.path_slug()
    decision = classify_resume(op, run_dir, expected_packet_sha256="a" * 64)
    assert decision == ResumeDecision.RUN


# --- post-blind resume is forbidden ----------------------------------


def test_post_blind_resume_is_forbidden(evaluator_with_open):
    with pytest.raises(RuntimeError, match="post-blind"):
        evaluator_with_open.resume(_op())


@pytest.fixture
def evaluator_with_open(tmp_path):
    code_path = tmp_path / "code.py"
    code_path.write_text("# frozen\n")
    ev = BlindEvaluator(
        root=tmp_path,
        code_paths=[code_path],
        eligible_windows=("W1", "W2", "W3", "W4"),
    )
    ev.freeze_all({
        w: [FrozenCandidate(window_id=w, stage_id=s,
                            candidate_id=f"{w}-{s}-C0",
                            candidate_set_sha256="a" * 64,
                            source_sha256="a" * 64, config_sha256="a" * 64)
             for s in ("G0", "G1", "G2", "G3")]
        for w in ("W1", "W2", "W3", "W4")
    })
    ev.open_blind(["W1", "W2", "W3", "W4"])
    return ev


def _sha(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
