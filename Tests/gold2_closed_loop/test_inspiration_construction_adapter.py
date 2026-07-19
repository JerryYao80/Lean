"""Failing contracts for the proof-local G3 inspiration-construction
adapter + G3 builder (Task 14, Phase 3 STOP gate).

Design invariants (spec §7 lines 262-282, §14 lines 418-421):

* The G3 builder consumes ONLY frozen evidence (the frozen review bundle)
  and window-local generation records. It does NOT read global history or
  the production inspiration machinery.
* The builder FREEZES the request/response/source/compiler/config hashes
  so a G3 candidate is reproducibly pinned to a frozen generation.
* It builds in an ISOLATED candidate directory; it never overwrites mature
  Gold2 / production state.
* Every outcome (generation, validation, compile, strategy, no-trade,
  training-gate) is registered and consumes budget.
* Failure modes map to the closed G3 alias reasons:
    - generation/compile failure  -> CONSTRUCTION_FAILED
    - training-gate rejection     -> CANDIDATE_REJECTED
  Rejection (CANDIDATE_REJECTED) reuses the G2 hash and caps the verdict
  at PARTIALLY_EFFECTIVE.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from Scripts.gold2_closed_loop.g3_builder import G3Builder, G3BuildResult, G3Request
from Scripts.gold2_closed_loop.state_machine import G3AliasReason


# --- fixtures ---------------------------------------------------------


@dataclass
class FakeGenerationResponse:
    """A frozen response from a fake generator."""
    source_text: str
    compiler_hash: str = "c0mp1ler" * 8
    config_hash: str = "c0nf1g" * 10
    compile_failed: bool = False
    training_gate_passed: bool = True


class FakeGenerator:
    """A generator that always succeeds and produces a fixed source."""
    def __init__(self) -> None:
        self.calls: list[G3Request] = []

    def __call__(self, request: G3Request) -> FakeGenerationResponse:
        self.calls.append(request)
        return FakeGenerationResponse(
            source_text="class G3 { } // generated",
        )


@pytest.fixture
def fake_generator() -> FakeGenerator:
    return FakeGenerator()


def _request() -> G3Request:
    return G3Request(
        experiment_id="E1",
        window_id="W1",
        stage_id="G3",
        candidate_id="G3-C0",
        partition="W1/train",
        input_evidence_sha256="e" * 64,
        parent_generation_id=None,
        generation_index=1,
        seed=17,
        budget=4,
    )


# --- success: freezes source hash ------------------------------------


def test_builder_freezes_generated_source(fake_generator, tmp_path):
    result = G3Builder(fake_generator).build(_request(), tmp_path / "proof")
    assert result.source_sha256
    assert len(result.source_sha256) == 64


def test_builder_records_all_frozen_hashes(fake_generator, tmp_path):
    result = G3Builder(fake_generator).build(_request(), tmp_path / "proof")
    assert result.request_sha256
    assert result.response_sha256
    assert result.source_sha256
    assert result.config_sha256
    assert result.candidate_set_sha256


def test_builder_writes_into_isolated_candidate_dir(fake_generator, tmp_path):
    proof_dir = tmp_path / "proof"
    result = G3Builder(fake_generator).build(_request(), proof_dir)
    # The candidate directory exists and the source was written there.
    assert result.candidate_dir is not None
    assert Path(result.candidate_dir).is_dir()
    # The source file was written (frozen source on disk).
    assert any(Path(result.candidate_dir).iterdir())


def test_builder_does_not_touch_mature_gold2(fake_generator, tmp_path):
    """The builder writes only under its isolated proof/candidate dir; it
    never writes to Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs or any
    production manifest/config."""
    proof_dir = tmp_path / "proof"
    mature = tmp_path / "Algorithm.CSharp" / "Gold2BetaVolTargetStrategy.cs"
    mature.parent.mkdir(parents=True)
    mature.write_text("# mature, must not change")
    G3Builder(fake_generator).build(_request(), proof_dir)
    assert mature.read_text() == "# mature, must not change"


def test_builder_success_sets_no_alias_reason(fake_generator, tmp_path):
    result = G3Builder(fake_generator).build(_request(), tmp_path / "proof")
    assert result.alias_reason is None
    assert result.eligible is True


# --- failure modes map to closed G3 alias reasons --------------------


class _FailGenerator:
    """A generator that fails at a named stage."""
    def __init__(self, failure: str) -> None:
        self.failure = failure

    def __call__(self, request: G3Request) -> Any:
        if self.failure == "generation":
            raise RuntimeError("generation failed")
        if self.failure == "compile":
            return FakeGenerationResponse(
                source_text="class G3 { }",
                compiler_hash="",  # empty compiler hash -> compile failure
            )
        if self.failure == "training_gate":
            return FakeGenerationResponse(
                source_text="class G3 { }",
                training_gate_passed=False,
            )
        return FakeGenerationResponse(source_text="class G3 { }")


def _failing_builder(failure: str, tmp_path: Path) -> G3BuildResult:
    builder = G3Builder(_FailGenerator(failure))
    return builder.build(_request(), tmp_path / "proof")


@pytest.mark.parametrize("failure,reason", [
    ("generation", "CONSTRUCTION_FAILED"),
    ("compile", "CONSTRUCTION_FAILED"),
    ("training_gate", "CANDIDATE_REJECTED"),
])
def test_closed_failure_reason(failure, reason, tmp_path):
    assert _failing_builder(failure, tmp_path).alias_reason == reason


def test_construction_failed_is_not_eligible(tmp_path):
    result = _failing_builder("generation", tmp_path)
    assert result.eligible is False
    assert result.alias_reason == G3AliasReason.CONSTRUCTION_FAILED.value


def test_candidate_rejected_reuses_g2_hash(tmp_path):
    """CANDIDATE_REJECTED reuses the G2 hash (the candidate did not pass the
    training gate, so the frozen version falls back to G2)."""
    req = _request()
    req_with_g2 = G3Request(
        experiment_id=req.experiment_id, window_id=req.window_id,
        stage_id=req.stage_id, candidate_id=req.candidate_id,
        partition=req.partition, input_evidence_sha256=req.input_evidence_sha256,
        parent_generation_id=None, generation_index=1, seed=17, budget=4,
        g2_candidate_set_sha256="g2hash" * 10,
    )
    result = G3Builder(_FailGenerator("training_gate")).build(
        req_with_g2, tmp_path / "proof"
    )
    assert result.alias_reason == G3AliasReason.CANDIDATE_REJECTED.value
    assert result.candidate_set_sha256 == "g2hash" * 10


def test_candidate_rejected_caps_at_partially_effective(tmp_path):
    """CANDIDATE_REJECTED caps the verdict at PARTIALLY_EFFECTIVE (spec §14
    line 421). The builder exposes a verdict_cap field the evaluator
    consumes."""
    req = _request()
    req_with_g2 = G3Request(
        experiment_id=req.experiment_id, window_id=req.window_id,
        stage_id=req.stage_id, candidate_id=req.candidate_id,
        partition=req.partition, input_evidence_sha256=req.input_evidence_sha256,
        parent_generation_id=None, generation_index=1, seed=17, budget=4,
        g2_candidate_set_sha256="g2hash" * 10,
    )
    result = G3Builder(_FailGenerator("training_gate")).build(
        req_with_g2, tmp_path / "proof"
    )
    assert result.verdict_cap == "PARTIALLY_EFFECTIVE"


# --- budget accounting ------------------------------------------------


def test_every_outcome_consumes_budget(fake_generator, tmp_path):
    """A successful build records exactly one attempt (one generation) that
    consumed one unit of budget."""
    result = G3Builder(fake_generator, budget=4).build(
        _request(), tmp_path / "proof"
    )
    assert result.attempted_trial_count == 1


def test_builder_never_imports_production_inspiration():
    """The builder module must NOT import Scripts.inspiration (the production
    overwrite-write generation machinery the proof forbids). Static check."""
    import Scripts.gold2_closed_loop.g3_builder as mod
    import inspect
    src = inspect.getsource(mod)
    assert "from Scripts.inspiration" not in src
    assert "import Scripts.inspiration" not in src


# --- isolation: path traversal / absolute candidate_id ----------------


def _req_with_id(cid: str) -> G3Request:
    r = _request()
    return G3Request(
        experiment_id=r.experiment_id, window_id=r.window_id,
        stage_id=r.stage_id, candidate_id=cid, partition=r.partition,
        input_evidence_sha256=r.input_evidence_sha256,
        parent_generation_id=None, generation_index=1, seed=17, budget=4,
    )


def test_absolute_candidate_id_is_rejected(fake_generator, tmp_path):
    """A candidate_id that is an absolute path must NOT escape the proof
    root (pathlib discards preceding components for an absolute final
    segment). Regression for the BLOCKER from the Task 14 review."""
    proof_dir = tmp_path / "proof"
    evil = tmp_path / "evil_abs_target"
    G3Builder(fake_generator).build(_req_with_id(str(evil)), proof_dir)
    # No G3.cs written outside the proof root.
    assert not (evil / "G3.cs").exists()
    # No candidates dir created under a path that resolved outside proof.
    assert evil.exists() is False or not (evil / "G3.cs").exists()


def test_traversal_candidate_id_is_rejected(fake_generator, tmp_path):
    """A candidate_id containing '..' must NOT escape proof_root/candidates/."""
    proof_dir = tmp_path / "proof"
    target = tmp_path / "escaped_sibling"
    target.mkdir()
    G3Builder(fake_generator).build(
        _req_with_id("../escaped_sibling"), proof_dir
    )
    # No G3.cs written into the sibling directory outside the proof root.
    assert not (target / "G3.cs").exists()


def test_rejected_candidate_id_maps_to_construction_failed(
    fake_generator, tmp_path
):
    """An escaping candidate_id maps to CONSTRUCTION_FAILED (not eligible),
    and the budget is still consumed."""
    result = G3Builder(fake_generator).build(
        _req_with_id("../escaped"), tmp_path / "proof"
    )
    assert result.eligible is False
    assert result.alias_reason == G3AliasReason.CONSTRUCTION_FAILED.value
    assert result.attempted_trial_count == 1


# --- empty / None source -> CONSTRUCTION_FAILED -----------------------


def test_empty_source_maps_to_construction_failed(tmp_path):
    """A generator returning empty/None source_text is a contract violation
    and must map to CONSTRUCTION_FAILED (not an eligible zero-content
    candidate). Regression for the Task 14 review."""

    class EmptyGen:
        def __call__(self, request):
            return FakeGenerationResponse(source_text="")

    result = G3Builder(EmptyGen()).build(_request(), tmp_path / "proof")
    assert result.eligible is False
    assert result.alias_reason == G3AliasReason.CONSTRUCTION_FAILED.value


# --- OSError during write -> CONSTRUCTION_FAILED ----------------------


def test_oserror_during_write_maps_to_construction_failed(
    fake_generator, tmp_path, monkeypatch
):
    """A disk-full / permission / file-blocks-dir OSError during the
    candidate write must map to CONSTRUCTION_FAILED, not propagate out of
    build() with no result. Regression for the Task 14 review."""
    import Scripts.gold2_closed_loop.g3_builder as mod

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(mod, "atomic_write_bytes", boom)
    result = G3Builder(fake_generator).build(_request(), tmp_path / "proof")
    assert result.eligible is False
    assert result.alias_reason == G3AliasReason.CONSTRUCTION_FAILED.value
    assert result.attempted_trial_count == 1
