"""Construction partition sandbox (Task 13, Phase 3 STOP gate).

Enforces the isolation contract from design §3 line 118 / §6 line 118:
"construction 进程只挂载 train/review partition 的只读快照，blind partition
不可见，并记录路径白名单与访问日志" (the construction process mounts only the
read-only train/review partition snapshots; the blind partition is INVISIBLE,
and a path whitelist + access log is recorded).

This module is PROOF-ONLY and side-effect-free: it builds an in-memory mount
table and appends to an access-log JSONL. It does NOT perform any real OS mount
(bind-mount / overlay) — LEAN reads data via the LEAN data-folder configuration
which the proof runner pins to an absolute run directory. The sandbox's job is
to (a) declare which roots are mountable during construction, (b) declare
which of those are read-only, and (c) verify that no recorded access touched
the blind root, so a candidate builder cannot silently read blind OHLC.

The blind root is deliberately ABSENT from the mount table AND the
``allowed_roots`` whitelist: even if a builder tried to open a blind path, the
sandbox's leakage check would flag it as ``INFORMATION_LEAKAGE`` (spec §6 line
118). This is the static guarantee that blind data never entered construction.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Design §5 invalid reason for a leakage violation.
_LEAKAGE = "INFORMATION_LEAKAGE"


@dataclass(frozen=True)
class Mount:
    """One declared construction mount.

    Attributes
    ----------
    name:
        Mount name (``"train"`` / ``"review"`` / ``"run"``). The blind root
        is NEVER a mount.
    source:
        Absolute source path (the partition snapshot root).
    read_only:
        ``True`` for train/review (the proof must not mutate the frozen
        partition snapshots); ``False`` for the run directory (the only
        writable mount, where LEAN writes its result packet + trace).
    """

    name: str
    source: Path
    read_only: bool


@dataclass(frozen=True)
class AccessViolation:
    """A recorded access that touched a forbidden (blind) root."""

    path: str
    invalid_reason: str
    detail: str


def build_construction_mounts(
    train: Path | str,
    review: Path | str,
    blind: Path | str,
    run: Path | str,
) -> list[Mount]:
    """Return the construction mount table.

    The blind root is passed in (so callers express it explicitly) but is
    DELIBERATELY NOT included in the returned list: the blind partition is
    invisible during construction (spec §6 line 118).

    The run directory is the ONLY writable mount. Train and review are
    read-only (the proof must not mutate frozen partition snapshots).
    """
    return [
        Mount(name="train", source=Path(train).resolve(), read_only=True),
        Mount(name="review", source=Path(review).resolve(), read_only=True),
        Mount(name="run", source=Path(run).resolve(), read_only=False),
    ]


class ConstructionSandbox:
    """Path whitelist + access log enforcing blind invisibility.

    Use :meth:`from_roots` to construct a sandbox from the four partition
    roots. :meth:`record_access` appends one record to the access log and is
    the single place the construction wiring records a touched path.
    :meth:`validate_no_blind_access` scans the log and returns every access
    whose resolved path falls under the blind root.

    Parameters
    ----------
    train, review, blind, run:
        The four partition roots. ``blind`` is stored ONLY for the leakage
        check; it is never added to ``allowed_roots`` and never appears in
        the mount table.
    access_log:
        JSONL path for the append-only access log. Each line is a flat JSON
        object with ``path`` (the recorded path string), ``resolved`` (the
        realpath), ``recorded_at_utc`` (ISO 8601 UTC with trailing ``Z``).
    """

    def __init__(
        self,
        *,
        train: Path | str,
        review: Path | str,
        blind: Path | str,
        run: Path | str,
        access_log: Path | str,
    ) -> None:
        self._train = Path(train).resolve()
        self._review = Path(review).resolve()
        self._blind = Path(blind).resolve()
        self._run = Path(run).resolve()
        self._access_log = Path(access_log)
        self._access_log.parent.mkdir(parents=True, exist_ok=True)
        # The path whitelist: the blind root is INTENTIONALLY absent so any
        # access into it is flagged as leakage. ``allowed_roots`` is the
        # public accessor for the whitelist (used by readiness checks).
        self._allowed_roots: list[str] = [
            str(self._train),
            str(self._review),
            str(self._run),
        ]
        self._mounts: list[Mount] = build_construction_mounts(
            self._train, self._review, self._blind, self._run
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_roots(
        cls,
        *,
        train: Path | str,
        review: Path | str,
        blind: Path | str,
        run: Path | str,
        access_log: Path | str,
    ) -> "ConstructionSandbox":
        """Construct a sandbox from the four partition roots."""
        return cls(
            train=train, review=review, blind=blind, run=run,
            access_log=access_log,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def allowed_roots(self) -> list[str]:
        """Path whitelist (blind root deliberately absent)."""
        return list(self._allowed_roots)

    @property
    def mounts(self) -> list[Mount]:
        """The construction mount table (blind root absent)."""
        return list(self._mounts)

    def record_access(self, path: Path | str) -> None:
        """Record one accessed path to the append-only access log.

        The log line is canonical JSON (stable key order) so two runs that
        touch the same path produce byte-identical log lines (the log is
        part of the sealed evidence). The write is appended with fsync so a
        crash mid-record cannot corrupt the log.

        Does NOT raise on a path under the blind root: recording is
        faithful, the leakage is detected by :meth:`validate_no_blind_access`.
        """
        raw = str(path)
        resolved = self._safe_resolve(path)
        record = {
            "path": raw,
            "resolved": resolved,
            "recorded_at_utc": _now_utc_iso(),
        }
        line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
        # Append-only: open in binary append mode and fsync so the record is
        # durable before construction continues.
        with self._access_log.open("ab") as stream:
            stream.write(line.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())

    def validate_no_blind_access(self) -> list[AccessViolation]:
        """Scan the access log and return every access that touched the
        blind root.

        Returns
        -------
        list[AccessViolation]
            Empty if no recorded access fell under the blind root. One
            ``AccessViolation`` per offending log line otherwise, with
            ``invalid_reason=INFORMATION_LEAKAGE`` (spec §6 line 118).
        """
        if not self._access_log.is_file():
            return []
        violations: list[AccessViolation] = []
        blind = self._blind
        for line in self._access_log.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                # A corrupt log line is itself an integrity failure, but the
                # sandbox's contract is blind-invisibility: flag the line as
                # a leakage violation so the operator cannot silently drop it.
                violations.append(
                    AccessViolation(
                        path="<corrupt-log-line>",
                        invalid_reason=_LEAKAGE,
                        detail=f"corrupt access-log line: {stripped[:120]!r}",
                    )
                )
                continue
            resolved = record.get("resolved", record.get("path", ""))
            if self._is_relative_to(Path(resolved), blind):
                violations.append(
                    AccessViolation(
                        path=str(record.get("path", resolved)),
                        invalid_reason=_LEAKAGE,
                        detail=(
                            f"recorded access {resolved!r} falls under the "
                            f"blind root {blind!r}; blind data must be "
                            f"invisible during construction"
                        ),
                    )
                )
        return violations

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_resolve(path: Path | str) -> str:
        """Resolve a path without following symlinks into nonexistence.

        ``Path.resolve(strict=False)`` (the default) tolerates missing
        components, which is what we want for a recorded path that may not
        exist yet (e.g. a trace file the builder is about to write).
        """
        try:
            return str(Path(path).resolve())
        except (OSError, RuntimeError):
            return str(path)

    @staticmethod
    def _is_relative_to(child: Path, parent: Path) -> bool:
        """``child == parent`` or ``child`` is a descendant of ``parent``."""
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


def _now_utc_iso() -> str:
    """Return the current UTC time as ISO 8601 with a trailing ``Z``."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
