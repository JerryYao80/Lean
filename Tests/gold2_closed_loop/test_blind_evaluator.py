"""Failing contracts for the global freeze + blind evaluator (Task 15,
Phase 4 STOP gate).

Design invariants (spec §5 lifecycle, §6 line 118 "全局一次冻结", §10
resume rules, §14):

* The blind partition opens ONLY after EVERY eligible window/stage
  identity+hash has been frozen (``blind_opened.json`` written atomically).
  Opening with an incomplete freeze raises "global candidate freeze
  incomplete".
* After ``BLIND_ACCESS_OPENED`` the evaluator revalidates every frozen
  hash (code/config/data/assembly/model). Any mutation invalidates the
  experiment with ``POST_BLIND_MUTATION`` (spec §5 line 223).
* After opening: NO generation, repair, retry, reselection, or config
  change is permitted; the evaluator executes the EXACT frozen candidates.
* Pre-open resume skips ONLY complete hash-valid operation IDs and
  quarantines partial directories.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from Scripts.gold2_closed_loop.blind_evaluator import (
    BlindEvaluator,
    FrozenCandidate,
    ImmutabilityResult,
)


# --- helpers ----------------------------------------------------------


def _frozen_candidate(window_id: str, stage: str = "G0",
                       sha: str = "a" * 64) -> FrozenCandidate:
    return FrozenCandidate(
        window_id=window_id,
        stage_id=stage,
        candidate_id=f"{window_id}-{stage}-C0",
        candidate_set_sha256=sha,
        source_sha256=sha,
        config_sha256=sha,
    )


def _frozen_sets() -> dict[str, list[FrozenCandidate]]:
    return {
        w: [_frozen_candidate(w, stage=s, sha=(w + s).encode().hex()[:64].ljust(64, "0"))
            for s in ("G0", "G1", "G2", "G3")]
        for w in ("W1", "W2", "W3", "W4")
    }


@pytest.fixture
def evaluator(tmp_path):
    code_path = tmp_path / "code.py"
    code_path.write_text("# frozen code\n", encoding="utf-8")
    return BlindEvaluator(
        root=tmp_path,
        code_paths=[code_path],
        eligible_windows=("W1", "W2", "W3", "W4"),
    )


# --- freeze + open ----------------------------------------------------


def test_blind_requires_every_eligible_window(evaluator):
    evaluator.freeze("W1", [_frozen_candidate("W1")])
    with pytest.raises(RuntimeError, match="global candidate freeze incomplete"):
        evaluator.open_blind(["W1", "W2", "W3"])


def test_freeze_all_then_open_writes_blind_opened(evaluator, tmp_path):
    evaluator.freeze_all(_frozen_sets())
    evaluator.open_blind(["W1", "W2", "W3", "W4"])
    assert (tmp_path / "blind_opened.json").is_file()


def test_open_blind_is_global_one_shot(evaluator):
    """BLIND_ACCESS_OPENED is a global one-shot: every eligible window must
    be frozen before ANY blind opens (spec §6 line 118)."""
    evaluator.freeze_all(_frozen_sets())
    evaluator.open_blind(["W1", "W2", "W3", "W4"])
    assert evaluator.blind_opened is True


def test_cannot_open_blind_twice(evaluator):
    evaluator.freeze_all(_frozen_sets())
    evaluator.open_blind(["W1", "W2", "W3", "W4"])
    with pytest.raises(RuntimeError, match="already opened"):
        evaluator.open_blind(["W1", "W2", "W3", "W4"])


# --- post-open immutability -------------------------------------------


def test_post_open_mutation_invalidates(evaluator, tmp_path):
    evaluator.freeze_all(_frozen_sets())
    evaluator.open_blind(["W1", "W2", "W3", "W4"])
    (tmp_path / "code.py").write_text("mutation")
    res = evaluator.validate_immutability()
    assert res.invalid_reason == "POST_BLIND_MUTATION"
    assert res.valid is False


def test_post_open_no_mutation_stays_valid(evaluator):
    evaluator.freeze_all(_frozen_sets())
    evaluator.open_blind(["W1", "W2", "W3", "W4"])
    res = evaluator.validate_immutability()
    assert res.valid is True
    assert res.invalid_reason is None


def test_post_open_artifact_mutation_invalidates(tmp_path):
    """A post-blind mutation to a registered candidate ARTIFACT (data /
    assembly / config / source file) is detected via the constructor's
    artifact_paths. Regression for the whole-tree review BLOCKER that the
    harness never registered artifact paths so data/assembly/config hashes
    were not revalidated post-blind."""
    code_path = tmp_path / "code.py"
    code_path.write_text("# frozen code\n")
    data_path = tmp_path / "frozen_data.csv"
    data_path.write_text("date,close\n2022-01-01,1.0\n")
    ev = BlindEvaluator(
        root=tmp_path, code_paths=[code_path],
        eligible_windows=("W1", "W2", "W3", "W4"),
        artifact_paths=[data_path],
    )
    ev.freeze_all(_frozen_sets())
    ev.open_blind(["W1", "W2", "W3", "W4"])
    # No mutation yet -> valid.
    assert ev.validate_immutability().valid is True
    # Mutate the data file post-blind -> POST_BLIND_MUTATION.
    data_path.write_text("date,close\n2022-01-01,2.0\n")
    res = ev.validate_immutability()
    assert res.valid is False
    assert res.invalid_reason == "POST_BLIND_MUTATION"
    assert str(data_path) in res.mutated_paths


def test_post_open_rejects_candidate_reselection(evaluator, tmp_path):
    """After opening, the evaluator must reject any attempt to reselect,
    regenerate, or retry a candidate (spec §14 line 424)."""
    evaluator.freeze_all(_frozen_sets())
    evaluator.open_blind(["W1", "W2", "W3", "W4"])
    with pytest.raises(RuntimeError, match="post-blind"):
        evaluator.assert_no_post_blind_mutation("candidate reselection attempted")


# --- execute exact frozen candidates ---------------------------------


def test_execute_runs_frozen_candidate_identity(evaluator, tmp_path):
    """The evaluator executes the EXACT frozen candidate (identity +
    candidate_set_sha256); it must reject a candidate whose hash does not
    match the frozen one."""
    evaluator.freeze_all(_frozen_sets())
    evaluator.open_blind(["W1", "W2", "W3", "W4"])
    frozen = evaluator.frozen_candidate("W1", "G0")
    # A matching identity + hash is accepted.
    assert evaluator.execute_frozen("W1", "G0",
                                    candidate_set_sha256=frozen.candidate_set_sha256,
                                    runner=lambda req: {"sharpe": 0.1})
    # A mismatched hash is rejected.
    with pytest.raises(RuntimeError, match="hash mismatch"):
        evaluator.execute_frozen("W1", "G0",
                                  candidate_set_sha256="0" * 64,
                                  runner=lambda req: {"sharpe": 0.1})


def test_execute_before_open_is_rejected(evaluator, tmp_path):
    evaluator.freeze_all(_frozen_sets())
    # Not opened yet.
    with pytest.raises(RuntimeError, match="not opened"):
        evaluator.execute_frozen("W1", "G0",
                                  candidate_set_sha256="a" * 64,
                                  runner=lambda req: {"sharpe": 0.1})
