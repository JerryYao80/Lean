"""Isolated G3 construction builder (Task 14, Phase 3 STOP gate).

Consumes ONLY frozen evidence (the frozen review bundle's
``input_evidence_sha256``) and a window-local generation record. The builder
is a thin, PROOF-ONLY orchestrator: it calls an injected ``generator``
callable to produce a candidate source, freezes the request/response/source/
compiler/config hashes, writes the source into an ISOLATED candidate
directory under the proof root, and maps every outcome to a closed
``G3AliasReason``.

Design invariants (spec §7 lines 262-282, §14 lines 418-421):

* The builder consumes ONLY frozen evidence + window-local generations; it
  does NOT read global history or the production
  ``Scripts/inspiration/generations.py`` machinery (spec §6 line 158 forbids
  reuse; that module uses overwrite-write and is non-collision-rejecting).
* It FREEZES the request/response/source/compiler/config hashes so a G3
  candidate is reproducibly pinned to a frozen generation (anti-p-hacking:
  the candidate is hash-pinned to the inputs that produced it).
* It builds in an ISOLATED candidate directory under the proof root; it
  NEVER overwrites mature Gold2 / production state
  (``Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs``, the production
  manifest/config). The candidate source is written to
  ``<proof_root>/candidates/<candidate_id>/G3.cs``, never to the
  ``Algorithm.CSharp`` tree.
* Every outcome (generation, validation, compile, strategy, no-trade,
  training-gate) is registered as an attempt and consumes budget. The
  ``attempted_trial_count`` is the budget consumed.
* Failure modes map to the closed G3 alias reasons:
    - generation/compile failure   -> ``CONSTRUCTION_FAILED``
    - training-gate rejection       -> ``CANDIDATE_REJECTED``
  ``CANDIDATE_REJECTED`` reuses the G2 candidate-set hash (the frozen
  version falls back to G2) and caps the verdict at
  ``PARTIALLY_EFFECTIVE`` (spec §14 line 421).

This module does NOT itself run the LEAN backtest or the training gate; it
produces a frozen candidate descriptor that the blind evaluator (Task 15)
executes. The training-gate outcome is injected via the generator's response
(``training_gate_passed``) so the builder stays a pure function of its
injected inputs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from Scripts.gold2_closed_loop.atomic_io import atomic_write_bytes
from Scripts.gold2_closed_loop.evidence import canonical_hash
from Scripts.gold2_closed_loop.state_machine import G3AliasReason, HistoricalVerdict

# The closed verdict cap applied when a G3 candidate is rejected by the
# training gate (spec §14 line 421: "最终最多 PARTIALLY_EFFECTIVE").
_REJECTED_VERDICT_CAP = HistoricalVerdict.PARTIALLY_EFFECTIVE.value


@dataclass(frozen=True)
class G3Request:
    """Typed request for a G3 candidate build.

    Carries the frozen-evidence identity (the review bundle's
    ``input_evidence_sha256``) plus the window-local generation identity, so
    a downstream reviewer cannot fabricate an identity it was never given.

    Fields
    ------
    experiment_id, window_id, stage_id, candidate_id, partition:
        Proof identity (mirrors the G1/G2 request).
    input_evidence_sha256:
        The frozen review-bundle evidence hash the G3 construction is
        pinned to (spec §6 line 246: generations are diagnostics over a
        SINGLE frozen bundle).
    parent_generation_id, generation_index:
        The window-local generation identity (mirrors GenerationRecord).
    seed, budget:
        Deterministic seed + the remaining candidate budget at the time
        of this request.
    g2_candidate_set_sha256:
        The G2 candidate-set hash to reuse on CANDIDATE_REJECTED (spec §7
        line 275: "无持续不收敛或候选被拒绝时，最终冻结版本可复用 G2 的哈希").
        Optional; required for the rejected path to fall back to G2.
    """

    experiment_id: str
    window_id: str
    stage_id: str
    candidate_id: str
    partition: str
    input_evidence_sha256: str
    parent_generation_id: str | None
    generation_index: int
    seed: int
    budget: int
    g2_candidate_set_sha256: str | None = None


@dataclass(frozen=True)
class G3BuildResult:
    """Result of :meth:`G3Builder.build`.

    Attributes
    ----------
    eligible:
        True iff the build produced a usable G3 candidate (the trigger
        fired and construction succeeded). False on CONSTRUCTION_FAILED /
        CANDIDATE_REJECTED.
    alias_reason:
        One of ``G3AliasReason`` values when ``eligible`` is False, else
        None.
    request_sha256 / response_sha256 / source_sha256 / config_sha256 /
    candidate_set_sha256:
        The frozen hashes pinning the candidate to its inputs. On
        CANDIDATE_REJECTED the candidate_set_sha256 is the G2 hash (the
        frozen version reuses G2).
    candidate_dir:
        Absolute path to the isolated candidate directory where the frozen
        source was written, or None on a generation failure (no source was
        produced).
    attempted_trial_count:
        The budget consumed (always 1; every build is one attempt).
    verdict_cap:
        The maximum historical verdict a CANDIDATE_REJECTED window may
        reach (``PARTIALLY_EFFECTIVE``). None when eligible or when the
        failure is CONSTRUCTION_FAILED (an execution failure, not a
        capped-valid result).
    """

    eligible: bool
    alias_reason: str | None
    request_sha256: str
    response_sha256: str
    source_sha256: str
    config_sha256: str
    candidate_set_sha256: str
    candidate_dir: str | None
    attempted_trial_count: int
    verdict_cap: str | None = None


class GenerationResponse(Protocol):
    """The injected generator's response contract.

    The generator returns an object with these attributes (a dataclass in
    production; the test fixtures use ``FakeGenerationResponse``). The
    builder reads them to compute the frozen response/source/config hashes
    and to detect the training-gate outcome.
    """

    source_text: str
    compiler_hash: str
    config_hash: str
    # Optional: the training-gate outcome. When False, the candidate was
    # built and compiled but the training gate rejected it -> CANDIDATE_REJECTED.
    # When None/True, the candidate passed the training gate -> eligible.
    training_gate_passed: Any


class G3Builder:
    """Build one G3 candidate from a frozen-evidence request.

    Parameters
    ----------
    generator:
        Callable ``generator(request) -> GenerationResponse``. May raise;
        a generation failure maps to ``CONSTRUCTION_FAILED``.
    budget:
        The candidate budget available for this G3 build (default 4). Each
        build consumes one unit.
    """

    def __init__(
        self,
        generator: Callable[[G3Request], Any],
        *,
        budget: int = 4,
    ) -> None:
        if budget < 1:
            raise ValueError(
                f"G3Builder: budget must be >= 1, got {budget}"
            )
        self._generator = generator
        self._budget = int(budget)

    def build(
        self, request: G3Request, proof_root: Path | str
    ) -> G3BuildResult:
        """Build one G3 candidate under ``proof_root`` and return the
        frozen descriptor.

        ``proof_root`` is the experiment's proof root; the candidate source
        is written to ``proof_root/candidates/<candidate_id>/G3.cs`` (an
        isolated directory). The mature Gold2 source and the production
        manifest/config are NEVER touched.
        """
        request_sha = canonical_hash(asdict(request))
        # Invoke the injected generator. A generation failure (raised
        # exception) maps to CONSTRUCTION_FAILED.
        try:
            response = self._generator(request)
        except Exception:
            return self._construction_failed(
                request_sha, candidate_dir=None,
                g2_fallback=request.g2_candidate_set_sha256,
            )
        if response is None:
            # Contract violation (the generator returned None): treat as a
            # construction failure, not a crash.
            return self._construction_failed(
                request_sha, candidate_dir=None,
                g2_fallback=request.g2_candidate_set_sha256,
            )
        response_sha = canonical_hash(_response_dict(response))
        source_text = getattr(response, "source_text", "")
        # A generator that omits / None / empty source_text is a contract
        # violation: a degenerate zero-content candidate must NOT be
        # hash-pinned and passed to the evaluator as eligible. Map it to
        # CONSTRUCTION_FAILED (no source was produced).
        if not isinstance(source_text, str) or not source_text.strip():
            return self._construction_failed(
                request_sha, candidate_dir=None,
                g2_fallback=request.g2_candidate_set_sha256,
                response_sha=response_sha,
            )
        source_sha = canonical_hash({"source_text": source_text})
        config_sha = canonical_hash({
            "compiler_hash": str(getattr(response, "compiler_hash", "") or ""),
            "config_hash": str(getattr(response, "config_hash", "") or ""),
        })
        # Resolve the ISOLATED candidate directory and GUARANTEE it stays
        # under proof_root/candidates/. The candidate_id is a free-form str
        # on the public request dataclass, so an id containing '..' or an
        # absolute path would otherwise escape the proof root (pathlib
        # discards preceding components for an absolute final segment) and
        # could write G3.cs outside the proof tree — or into
        # Algorithm.CSharp. Reject any such id as CONSTRUCTION_FAILED.
        candidate_root = (Path(proof_root) / "candidates").resolve()
        try:
            candidate_dir = (candidate_root / request.candidate_id).resolve()
        except (OSError, ValueError):
            return self._construction_failed(
                request_sha, candidate_dir=None,
                g2_fallback=request.g2_candidate_set_sha256,
                response_sha=response_sha, source_sha=source_sha,
                config_sha=config_sha,
            )
        try:
            candidate_dir.relative_to(candidate_root)
        except ValueError:
            # The resolved candidate_dir is not under candidate_root: the
            # candidate_id contained '..' / an absolute path. Refuse to
            # write outside the proof root.
            return self._construction_failed(
                request_sha, candidate_dir=None,
                g2_fallback=request.g2_candidate_set_sha256,
                response_sha=response_sha, source_sha=source_sha,
                config_sha=config_sha,
            )
        # mkdir + atomic write. Any OSError here (disk full, permission,
        # a file blocking the dir, quota) is an execution failure: map it
        # to CONSTRUCTION_FAILED so the candidate is closed with a terminal
        # alias reason and the budget is consumed, rather than propagating
        # out of build() with no result recorded.
        try:
            candidate_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(
                candidate_dir / "G3.cs", source_text.encode("utf-8")
            )
        except OSError:
            return self._construction_failed(
                request_sha, candidate_dir=str(candidate_dir),
                g2_fallback=request.g2_candidate_set_sha256,
                response_sha=response_sha, source_sha=source_sha,
                config_sha=config_sha,
            )
        candidate_set_sha = canonical_hash({
            "source_sha256": source_sha,
            "config_sha256": config_sha,
            "candidate_id": request.candidate_id,
        })
        # Compile step: the response may signal a compile failure directly
        # (``compile_failed=True``) or by an empty compiler_hash. Both map
        # to CONSTRUCTION_FAILED.
        compile_failed = bool(getattr(response, "compile_failed", False))
        compiler_hash = str(getattr(response, "compiler_hash", "") or "")
        if compile_failed or not compiler_hash:
            return self._construction_failed(
                request_sha, candidate_dir=str(candidate_dir),
                g2_fallback=request.g2_candidate_set_sha256,
                response_sha=response_sha, source_sha=source_sha,
                config_sha=config_sha,
            )
        # Training-gate outcome (injected via the response): a built-and-
        # compiled candidate that the training gate rejects maps to
        # CANDIDATE_REJECTED and reuses the G2 hash.
        #
        # Use truthiness (``not gate_passed``), NOT identity (``is False``):
        # the production generator may carry the gate outcome from the
        # numpy/pandas-heavy auto_optimize pipeline as a ``numpy.bool_(False)``
        # or an int ``0``. ``numpy.bool_(False) is False`` is False (it is a
        # distinct type), so an identity check would let a rejected gate
        # silently pass as eligible, skipping the CANDIDATE_REJECTED alias,
        # the G2-hash reuse, and the PARTIALLY_EFFECTIVE verdict cap (spec
        # §14 line 421). Truthiness correctly treats every falsy value as a
        # rejection; the only falsy-but-valid sentinel we must NOT misread
        # is absent (the gate outcome is a boolean pass/fail).
        gate_passed = getattr(response, "training_gate_passed", True)
        if not gate_passed:
            g2_hash = request.g2_candidate_set_sha256
            fallback = g2_hash if g2_hash else candidate_set_sha
            return G3BuildResult(
                eligible=False,
                alias_reason=G3AliasReason.CANDIDATE_REJECTED.value,
                request_sha256=request_sha,
                response_sha256=response_sha,
                source_sha256=source_sha,
                config_sha256=config_sha,
                candidate_set_sha256=fallback,
                candidate_dir=str(candidate_dir),
                attempted_trial_count=1,
                verdict_cap=_REJECTED_VERDICT_CAP,
            )
        return G3BuildResult(
            eligible=True,
            alias_reason=None,
            request_sha256=request_sha,
            response_sha256=response_sha,
            source_sha256=source_sha,
            config_sha256=config_sha,
            candidate_set_sha256=candidate_set_sha,
            candidate_dir=str(candidate_dir),
            attempted_trial_count=1,
            verdict_cap=None,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _construction_failed(
        request_sha: str,
        *,
        candidate_dir: str | None,
        g2_fallback: str | None,
        response_sha: str = "",
        source_sha: str = "",
        config_sha: str = "",
    ) -> G3BuildResult:
        """Build a CONSTRUCTION_FAILED result (no usable candidate set)."""
        return G3BuildResult(
            eligible=False,
            alias_reason=G3AliasReason.CONSTRUCTION_FAILED.value,
            request_sha256=request_sha,
            response_sha256=response_sha,
            source_sha256=source_sha,
            config_sha256=config_sha,
            candidate_set_sha256="0" * 64,
            candidate_dir=candidate_dir,
            attempted_trial_count=1,
            verdict_cap=None,
        )


def _response_dict(response: Any) -> dict[str, Any]:
    """Extract the frozen-response fields from a generator response object.

    Only the hash-relevant fields are extracted; the source_text is hashed
    separately (it is the primary economic artifact). ``training_gate_passed``
    is NOT part of the response hash (it is an outcome, not an input).
    """
    return {
        "source_text": str(getattr(response, "source_text", "") or ""),
        "compiler_hash": str(getattr(response, "compiler_hash", "") or ""),
        "config_hash": str(getattr(response, "config_hash", "") or ""),
    }


__all__ = [
    "G3Builder",
    "G3BuildResult",
    "G3Request",
    "GenerationResponse",
]
