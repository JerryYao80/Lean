"""Pure artifact helpers for the isolated LEAN proof runner (Task 7).

No subprocess, no side effects. These helpers load the EXACT result packet
by run id (never glob, never mtime) and compute streaming hashes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Status enum values for LeanExecutionResult.
SUCCEEDED = "SUCCEEDED"
FAILED_NONZERO_EXIT = "FAILED_NONZERO_EXIT"
FAILED_TIMEOUT = "FAILED_TIMEOUT"
FAILED_EVIDENCE_CAPTURE = "FAILED_EVIDENCE_CAPTURE"
FAILED_MISSING_PACKET = "FAILED_MISSING_PACKET"
FAILED_STALE_PACKET = "FAILED_STALE_PACKET"

_ALL_STATUSES = frozenset(
    {
        SUCCEEDED,
        FAILED_NONZERO_EXIT,
        FAILED_TIMEOUT,
        FAILED_EVIDENCE_CAPTURE,
        FAILED_MISSING_PACKET,
        FAILED_STALE_PACKET,
    }
)

_HASH_CHUNK = 1024 * 1024  # 1 MiB


@dataclass(frozen=True)
class LeanExecutionResult:
    """Immutable record of one isolated LEAN backtest execution.

    Fields
    ------
    run_id:
        The deterministic algorithm-id used to locate the exact packet.
    status:
        One of the module-level status constants.
    exit_code:
        The dotnet exit code, or None if the process timed out / never ran.
    config_path:
        Absolute path to the staging config JSON written by the runner.
    packet_path:
        Absolute path to the EXACT ``<run_id>.json`` packet, or None if missing.
    packet_sha256:
        Streaming SHA-256 of the packet file, or None if missing.
    trace_path:
        Absolute path to the formal trace JSONL, or None if not configured.
    trace_sha256:
        Streaming SHA-256 of the trace file, or None if missing.
    elapsed_seconds:
        Wall-clock seconds the subprocess ran (excluding staging).
    error:
        Tail of stdout/stderr or a human-readable error; None on clean success.
    """

    run_id: str
    status: str
    exit_code: int | None
    config_path: str | None = None
    packet_path: str | None = None
    packet_sha256: str | None = None
    trace_path: str | None = None
    trace_sha256: str | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _ALL_STATUSES:
            raise ValueError(
                f"unknown LeanExecutionResult status {self.status!r}; "
                f"expected one of {sorted(_ALL_STATUSES)}"
            )

    def is_success(self) -> bool:
        return self.status == SUCCEEDED


def sha256_file(path: Path | str) -> str:
    """Streaming SHA-256 of a file in 1 MiB chunks."""
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(_HASH_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def load_exact_packet(run_dir: Path | str, run_id: str) -> dict[str, Any]:
    """Load EXACTLY ``<run_dir>/<run_id>.json`` — never glob, never mtime.

    Raises
    ------
    FileNotFoundError:
        If the exact packet is missing (message contains "expected result packet").
    ValueError:
        If the packet exists but is empty or malformed JSON.
    """
    run_dir = Path(run_dir)
    packet_path = run_dir / f"{run_id}.json"
    if not packet_path.is_file():
        raise FileNotFoundError(
            f"expected result packet {packet_path} not found in {run_dir}; "
            f"refusing to glob or select by mtime"
        )
    raw = packet_path.read_bytes()
    if not raw.strip():
        raise ValueError(f"expected result packet {packet_path} is empty")
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"expected result packet {packet_path} is malformed JSON: {error}"
        ) from error
    if not isinstance(loaded, dict):
        raise ValueError(
            f"expected result packet {packet_path} is not a JSON object"
        )
    return loaded


def classify_outcome(
    *,
    timed_out: bool,
    exit_code: int | None,
    packet_exists: bool,
    trace_exists: bool,
    trace_nonempty: bool,
    trace_path_given: bool,
) -> str:
    """Classify a raw process outcome into a LeanExecutionResult status.

    Classification rules (deterministic, documented):

    * TimeoutExpired                        -> FAILED_TIMEOUT
    * Packet missing                         -> FAILED_MISSING_PACKET
      (regardless of exit code; LEAN may exit 0 but fail to write, or exit
      nonzero and never reach StoreResult.)
    * Nonzero exit + trace configured + (trace missing OR empty) -> FAILED_EVIDENCE_CAPTURE
      The proof strategy's OnEndOfAlgorithm FlushAndReconcile gate threw
      before completing the trace, which propagated as the runtime error that
      caused the nonzero exit. This is the spec's evidence-capture failure.
    * Nonzero exit + trace present+nonempty (or no trace configured) -> FAILED_NONZERO_EXIT
      A generic crash AFTER evidence was captured.
    * Zero exit + packet present            -> SUCCEEDED

    FAILED_STALE_PACKET is reserved for Task 8 validation; this helper never
    returns it because Task 7 never globs and never substitutes a stale packet.
    """
    if timed_out:
        return FAILED_TIMEOUT
    if not packet_exists:
        return FAILED_MISSING_PACKET
    if exit_code is not None and exit_code != 0:
        if trace_path_given and (not trace_exists or not trace_nonempty):
            return FAILED_EVIDENCE_CAPTURE
        return FAILED_NONZERO_EXIT
    return SUCCEEDED
