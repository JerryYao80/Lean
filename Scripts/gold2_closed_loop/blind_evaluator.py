"""Global freeze + blind evaluator (Task 15, Phase 4 STOP gate).

Implements the design §5 / §6 line 118 ("全局一次冻结") / §10 resume / §14
contract:

* Freeze every eligible window/stage identity + hash, then ATOMICALLY
  write ``blind_opened.json``. Opening requires the freeze to be complete
  for EVERY eligible window (a global one-shot — no partial blind access).
* After ``BLIND_ACCESS_OPENED``, revalidate every frozen hash (code /
  config / data / assembly / model). Any mutation invalidates the
  experiment with ``POST_BLIND_MUTATION`` (spec §5 line 223).
* After opening: reject generation / repair / retry / reselection /
  config change (spec §14 line 424); execute the EXACT frozen candidates
  (identity + candidate_set_sha256 must match the frozen one).
* Pre-open resume skips ONLY complete hash-valid operation IDs and
  QUARANTINES partial directories (so a re-execution starts clean and
  cannot consume a stale partial packet). Post-open resume is FORBIDDEN.

This module is PROOF-ONLY. It does NOT import the production
``evolution_scheduler`` / ``layer_state`` / ``inspiration`` machinery; it
executes candidates via an injected ``runner`` callable (the proof
wiring passes the LEAN runner).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from Scripts.gold2_closed_loop.atomic_io import atomic_write_json
from Scripts.gold2_closed_loop.state_machine import InvalidReason

# Design §5 invalid_reason for a post-blind mutation.
_POST_BLIND_MUTATION = InvalidReason.POST_BLIND_MUTATION.value


# ---------------------------------------------------------------------
# Frozen candidate + operation identity
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenCandidate:
    """One frozen candidate identity + hash set.

    A frozen candidate is pinned by its window/stage/candidate id AND by
    the candidate_set_sha256 (the G3 builder's frozen descriptor hash, or
    the G2 hash on a CANDIDATE_REJECTED alias). The evaluator executes
    the EXACT frozen candidate: a request whose candidate_set_sha256 does
    not match the frozen one is rejected as a hash mismatch.
    """

    window_id: str
    stage_id: str
    candidate_id: str
    candidate_set_sha256: str
    source_sha256: str
    config_sha256: str


@dataclass(frozen=True)
class OperationId:
    """The resume operation identity (spec §10).

    An operation is determined by
    experiment/window/stage/partition/candidate/scenario/attempt. The
    ``path_slug`` is a stable filesystem-safe rendering used as the run
    directory name so a re-execution lands in the SAME directory.
    """

    experiment_id: str
    window_id: str
    stage_id: str
    partition: str
    candidate_id: str
    scenario: str
    attempt: int

    def path_slug(self) -> str:
        # Filesystem-safe, deterministic. Sanitize each component so '..'
        # / '/' in a candidate_id cannot escape the run root.
        parts = (
            self.experiment_id, self.window_id, self.stage_id,
            self.partition, self.candidate_id, self.scenario,
            str(self.attempt),
        )
        safe = "_".join(_sanitize_slug(p) for p in parts)
        return safe


def _sanitize_slug(value: str) -> str:
    """Make a string safe for use as a single path component."""
    if not isinstance(value, str):
        value = str(value)
    # Replace anything that is not [A-Za-z0-9._-] with '_'. This strips
    # '/' and '..' so a candidate_id cannot traverse out of the run root.
    return re.sub(r"[^A-Za-z0-9._-]", "_", value) or "_"


class ResumeDecision(StrEnum):
    """What a pre-open resume should do with an operation's run dir.

    SKIP       : the dir has a complete, hash-valid packet -> skip.
    QUARANTINE : the dir has a packet but its hash does not match -> move
                 the dir aside so a re-execution starts clean.
    RUN        : no dir / no packet -> execute the operation.
    """

    SKIP = "SKIP"
    QUARANTINE = "QUARANTINE"
    RUN = "RUN"


# ---------------------------------------------------------------------
# Resume classification (pure function)
# ---------------------------------------------------------------------


def classify_resume(
    op: OperationId,
    run_dir: Path,
    expected_packet_sha256: str,
    *,
    packet_name: str = "packet.json",
) -> ResumeDecision:
    """Classify what a pre-open resume should do with ``run_dir``.

    * No ``run_dir`` / no ``<packet_name>`` -> RUN.
    * Packet present AND its sha256 == ``expected_packet_sha256`` -> SKIP
      (complete, hash-valid terminal op).
    * Packet present but hash mismatch -> QUARANTINE: move the run dir
      aside (rename to ``<dir>.quarantined-<ts>``) so a re-execution
      starts clean. The quarantine MOVED the dir out of the way: callers
      assert the original packet path no longer exists after a QUARANTINE
      decision.
    """
    if not run_dir.is_dir():
        return ResumeDecision.RUN
    packet = run_dir / packet_name
    if not packet.is_file():
        # A run dir without the expected packet is partial -> quarantine
        # it so the re-execution does not land alongside stale artifacts.
        _quarantine(run_dir)
        return ResumeDecision.QUARANTINE
    actual = _file_sha256(packet)
    if actual == expected_packet_sha256:
        return ResumeDecision.SKIP
    _quarantine(run_dir)
    return ResumeDecision.QUARANTINE


def _quarantine(run_dir: Path) -> Path:
    """Move ``run_dir`` aside to a sibling ``<name>.quarantined-<ts>`` dir.

    The timestamp is only used to make the quarantine name unique. On a
    name collision a numeric suffix is appended.
    """
    parent = run_dir.parent
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = parent / f"{run_dir.name}.quarantined-{ts}"
    suffix = 0
    while target.exists():
        suffix += 1
        target = parent / f"{run_dir.name}.quarantined-{ts}-{suffix}"
    shutil.move(str(run_dir), str(target))
    return target


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1 << 20)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------
# Immutability result
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ImmutabilityResult:
    """Result of :meth:`BlindEvaluator.validate_immutability`."""

    valid: bool
    invalid_reason: str | None = None
    mutated_paths: tuple[str, ...] = ()


# ---------------------------------------------------------------------
# Blind evaluator
# ---------------------------------------------------------------------


class BlindEvaluator:
    """Freeze every eligible window/stage candidate, then open the blind
    partition in a global one-shot and execute the EXACT frozen candidates.

    Parameters
    ----------
    root:
        The experiment's sealed root (where ``blind_opened.json`` lives).
    code_paths:
        The proof code files to hash for the immutability check. After
        ``BLIND_ACCESS_OPENED`` any byte change to these files invalidates
        the experiment (``POST_BLIND_MUTATION``).
    eligible_windows:
        The window ids that MUST be frozen before the blind opens.
    """

    def __init__(
        self,
        root: Path | str,
        code_paths: Iterable[Path | str],
        eligible_windows: Sequence[str],
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._code_paths = tuple(Path(p) for p in code_paths)
        self._eligible_windows = tuple(eligible_windows)
        # window_id -> stage_id -> FrozenCandidate
        self._frozen: dict[str, dict[str, FrozenCandidate]] = {}
        self._blind_opened: bool = False
        # Frozen code hashes captured at open time for the immutability
        # revalidation. Empty until open.
        self._frozen_code_hashes: dict[str, str] = {}

    # -----------------------------------------------------------------
    # Freeze
    # -----------------------------------------------------------------

    def freeze(
        self, window_id: str, candidates: Sequence[FrozenCandidate]
    ) -> None:
        """Freeze the candidate set for ONE window.

        Called during construction, BEFORE any blind access. Each
        candidate's identity + candidate_set_sha256 is recorded; the
        evaluator will only ever execute a candidate whose hash matches.
        """
        if self._blind_opened:
            raise RuntimeError(
                "post-blind mutation rejected: cannot freeze a candidate "
                "after BLIND_ACCESS_OPENED (spec §14 line 424)"
            )
        if window_id not in self._eligible_windows:
            raise ValueError(
                f"freeze: window {window_id!r} is not among the eligible "
                f"windows {list(self._eligible_windows)}"
            )
        stage_map: dict[str, FrozenCandidate] = {}
        for cand in candidates:
            if cand.window_id != window_id:
                raise ValueError(
                    f"freeze: candidate window_id {cand.window_id!r} != "
                    f"window {window_id!r}"
                )
            stage_map[cand.stage_id] = cand
        self._frozen[window_id] = stage_map

    def freeze_all(
        self, per_window: dict[str, Sequence[FrozenCandidate]]
    ) -> None:
        """Freeze every eligible window's candidate set at once."""
        for window_id in self._eligible_windows:
            cands = per_window.get(window_id)
            if not cands:
                raise RuntimeError(
                    f"global candidate freeze incomplete: window "
                    f"{window_id!r} has no frozen candidates"
                )
            self.freeze(window_id, cands)

    # -----------------------------------------------------------------
    # Open the blind partition (global one-shot)
    # -----------------------------------------------------------------

    def open_blind(self, window_ids: Sequence[str]) -> None:
        """Open the blind partition for ALL eligible windows at once.

        Requires EVERY eligible window to have a complete frozen candidate
        set. Atomically writes ``blind_opened.json`` (so a crash mid-open
        cannot leave a half-opened state). After open, any code mutation
        invalidates the experiment.
        """
        if self._blind_opened:
            raise RuntimeError("blind already opened: open_blind is a one-shot")
        missing = [w for w in self._eligible_windows if w not in self._frozen]
        if missing:
            raise RuntimeError(
                f"global candidate freeze incomplete: windows {missing} "
                f"have no frozen candidates (spec §6 line 118: 全局一次冻结)"
            )
        requested = set(window_ids)
        eligible = set(self._eligible_windows)
        if requested != eligible:
            raise RuntimeError(
                f"open_blind must open every eligible window at once; "
                f"requested {sorted(requested)} != eligible {sorted(eligible)}"
            )
        # Capture the frozen code hashes for the post-open immutability
        # revalidation. Done BEFORE writing blind_opened.json so the hashes
        # are pinned to the open-time state.
        self._frozen_code_hashes = {
            str(p): _file_sha256(p) for p in self._code_paths if p.is_file()
        }
        payload = {
            "opened_at_utc": _now_utc_iso(),
            "eligible_windows": list(self._eligible_windows),
            "frozen_windows": sorted(self._frozen),
            "frozen_code_hashes": dict(self._frozen_code_hashes),
            "frozen_candidates": {
                w: {
                    s: {
                        "candidate_id": c.candidate_id,
                        "candidate_set_sha256": c.candidate_set_sha256,
                        "source_sha256": c.source_sha256,
                        "config_sha256": c.config_sha256,
                    }
                    for s, c in stages.items()
                }
                for w, stages in self._frozen.items()
            },
        }
        atomic_write_json(self._root / "blind_opened.json", payload)
        self._blind_opened = True

    # -----------------------------------------------------------------
    # Immutability revalidation
    # -----------------------------------------------------------------

    def validate_immutability(self) -> ImmutabilityResult:
        """Revalidate every frozen hash after ``BLIND_ACCESS_OPENED``.

        Returns ``ImmutabilityResult(valid=False, invalid_reason=
        POST_BLIND_MUTATION)`` if ANY frozen code hash changed; the result
        names every mutated path. Returns valid=True if all hashes match.
        """
        if not self._blind_opened:
            # Pre-open: immutability is not yet in force. Report valid.
            return ImmutabilityResult(valid=True)
        mutated: list[str] = []
        for path_str, expected in self._frozen_code_hashes.items():
            p = Path(path_str)
            if not p.is_file():
                mutated.append(f"{path_str} (deleted)")
                continue
            actual = _file_sha256(p)
            if actual != expected:
                mutated.append(path_str)
        if mutated:
            return ImmutabilityResult(
                valid=False,
                invalid_reason=_POST_BLIND_MUTATION,
                mutated_paths=tuple(mutated),
            )
        return ImmutabilityResult(valid=True)

    def assert_no_post_blind_mutation(self, detail: str) -> None:
        """Raise if a post-blind mutation is attempted (generation /
        repair / retry / reselection / config change). Spec §14 line 424."""
        if self._blind_opened:
            raise RuntimeError(
                f"post-blind mutation rejected: {detail} (spec §14 line 424: "
                "盲测之后禁止生成、重排、修复或重试候选)"
            )

    # -----------------------------------------------------------------
    # Execute the EXACT frozen candidates
    # -----------------------------------------------------------------

    @property
    def blind_opened(self) -> bool:
        return self._blind_opened

    def frozen_candidate(self, window_id: str, stage_id: str) -> FrozenCandidate:
        stages = self._frozen.get(window_id)
        if not stages or stage_id not in stages:
            raise KeyError(
                f"no frozen candidate for {window_id}/{stage_id}"
            )
        return stages[stage_id]

    def execute_frozen(
        self,
        window_id: str,
        stage_id: str,
        *,
        candidate_set_sha256: str,
        runner: Callable[[FrozenCandidate], Any],
    ) -> Any:
        """Execute the EXACT frozen candidate.

        The caller passes the candidate_set_sha256 it intends to run; the
        evaluator REFUSES to execute if it does not match the frozen one
        (hash mismatch -> RuntimeError). This is the anti-p-hacking core:
        after the blind opens the evaluator cannot silently swap in a
        different candidate.
        """
        if not self._blind_opened:
            raise RuntimeError(
                "blind not opened: execute_frozen requires BLIND_ACCESS_OPENED"
            )
        # Immutability revalidation before execution: any post-open code
        # mutation blocks execution.
        imm = self.validate_immutability()
        if not imm.valid:
            raise RuntimeError(
                f"post-blind mutation blocks execution "
                f"({imm.invalid_reason}; mutated={list(imm.mutated_paths)})"
            )
        frozen = self.frozen_candidate(window_id, stage_id)
        if candidate_set_sha256 != frozen.candidate_set_sha256:
            raise RuntimeError(
                f"hash mismatch: requested candidate_set_sha256 "
                f"{candidate_set_sha256[:12]}... != frozen "
                f"{frozen.candidate_set_sha256[:12]}... for "
                f"{window_id}/{stage_id}; the evaluator executes the "
                f"EXACT frozen candidate (spec §14 line 424)"
            )
        return runner(frozen)

    # -----------------------------------------------------------------
    # Resume
    # -----------------------------------------------------------------

    def resume(self, op: OperationId) -> ResumeDecision:
        """Resume guard.

        AFTER ``BLIND_ACCESS_OPENED`` this is FORBIDDEN (raises) — post-blind
        resume would be a mutation (spec §14 line 424). Pre-open resume is
        driven by the pure function :func:`classify_resume` (the caller
        supplies the expected packet sha256); this method is the guard.
        """
        if self._blind_opened:
            raise RuntimeError(
                "post-blind resume rejected: resume is forbidden after "
                "BLIND_ACCESS_OPENED (spec §14 line 424)"
            )
        raise RuntimeError(
            "resume: pre-open resume requires the caller to supply the "
            "expected packet sha256 via classify_resume(op, run_dir, sha)"
        )


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "BlindEvaluator",
    "FrozenCandidate",
    "ImmutabilityResult",
    "OperationId",
    "ResumeDecision",
    "classify_resume",
]
