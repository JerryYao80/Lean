"""Evidence seal (Task 17, Phase 4 STOP gate).

Indexes every evidence artifact under an experiment root, computes a
canonical root hash, signs it with an INJECTED external signer, and
publishes it to an INJECTED append-only publisher (spec §15 lines 426-443).

The seal record carries:
* the root hash (SHA-256 of the canonical index of every artifact's path
  + sha256);
* the signing algorithm + public-key fingerprint (from the signer);
* a trusted timestamp;
* the publication receipt (from the publisher);
* the full index (every artifact's relative path + sha256).

Design invariants:
* The seal REFUSES a missing failed-candidate artifact: a failed candidate
  MUST be present before sealing (spec §15 line 443: "必须先封存完整负结果").
* A mutation to ANY indexed artifact after sealing breaks the seal
  (``verify_seal`` returns False) — the root hash is recomputed and must
  match.
* The publisher is append-only: republishing the same root hash is a
  violation (the production publisher publishes to an external append-only
  location; spec §15 line 443).

The signer + publisher are INJECTED (Protocol-typed) so the proof never
hard-codes a signing key; the production wiring injects an external key
reference + an external append-only publisher. Tests inject fakes.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from Scripts.gold2_closed_loop.atomic_io import atomic_write_json
from Scripts.gold2_closed_loop.evidence import canonical_hash

_HASH_CHUNK = 1024 * 1024
_ALGORITHM = "SHA-256-canonical-JSON"


# ---------------------------------------------------------------------
# Signer + publisher protocols
# ---------------------------------------------------------------------


class Signer(Protocol):
    """External signer: signs the root hash and returns (signature,
    public_key_fingerprint). Production injects a reference to an external
    key; tests inject a deterministic fake."""

    def sign(self, root_hash: str) -> tuple[str, str]: ...


class Publisher(Protocol):
    """External append-only publisher: publishes the seal record and
    returns a receipt. Republishing the same root hash is a violation
    (the publisher raises)."""

    def publish(self, record: dict[str, Any]) -> str: ...


# ---------------------------------------------------------------------
# Seal record
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class IndexEntry:
    path: str  # relative to the evidence root
    sha256: str


@dataclass(frozen=True)
class SealRecord:
    root_hash: str
    algorithm: str
    signature: str
    public_key_fingerprint: str
    trusted_time: str
    receipt: str
    index: tuple[IndexEntry, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------
# Build the seal
# ---------------------------------------------------------------------


def build_seal(
    evidence_root: Path | str,
    signer: Signer,
    publisher: Publisher,
) -> SealRecord:
    """Index every artifact under ``evidence_root``, compute the root hash,
    sign + publish it, and return the seal record.

    Raises ``ValueError`` if a failed candidate is missing from the
    evidence tree (spec §15 line 443: the seal must preserve complete
    negative results before interpreting them).
    """
    root = Path(evidence_root)
    if not root.is_dir():
        raise FileNotFoundError(f"evidence root not found: {root}")
    index = _build_index(root)
    _require_failed_candidate_present(root, index)
    root_hash = _root_hash(index)
    signature, pkfp = signer.sign(root_hash)
    trusted_time = _now_utc_iso()
    record_payload = {
        "root_hash": root_hash,
        "algorithm": _ALGORITHM,
        "signature": signature,
        "public_key_fingerprint": pkfp,
        "trusted_time": trusted_time,
        "index": [{"path": e.path, "sha256": e.sha256} for e in index],
    }
    receipt = publisher.publish(record_payload)
    seal = SealRecord(
        root_hash=root_hash,
        algorithm=_ALGORITHM,
        signature=signature,
        public_key_fingerprint=pkfp,
        trusted_time=trusted_time,
        receipt=receipt,
        index=tuple(index),
    )
    # Persist the seal under the evidence root so verify-seal can read it.
    atomic_write_json(root / "seal.json", _seal_to_dict(seal))
    return seal


def verify_seal(evidence_root: Path | str, seal: SealRecord) -> bool:
    """Recompute the root hash from the evidence tree and compare it to
    the seal's root hash. Returns True iff every indexed artifact is
    unchanged (byte-for-byte)."""
    root = Path(evidence_root)
    if not root.is_dir():
        return False
    try:
        current_index = _build_index(root)
    except OSError:
        return False
    current_hash = _root_hash(current_index)
    return current_hash == seal.root_hash


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------


def _build_index(root: Path) -> list[IndexEntry]:
    """Walk ``root`` recursively and hash every file in 1 MiB chunks.

    The index is sorted by relative path so the root hash is
    order-independent.
    """
    entries: list[IndexEntry] = []
    for path in sorted(_walk_files(root)):
        rel = path.relative_to(root).as_posix()
        sha = _file_sha256(path)
        entries.append(IndexEntry(path=rel, sha256=sha))
    return entries


def _walk_files(root: Path) -> Iterable[Path]:
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            # Skip the seal.json itself (it is the output of sealing, not
            # an evidence artifact). It is written AFTER the index is
            # built, so this guard is defensive against re-sealing.
            if p.name == "seal.json" and p.parent == root:
                continue
            if p.is_file():
                yield p


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(_HASH_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _root_hash(index: list[IndexEntry]) -> str:
    """Canonical SHA-256 of the sorted index (path + sha256 per entry)."""
    payload = [{"path": e.path, "sha256": e.sha256} for e in index]
    return canonical_hash(payload)


def _require_failed_candidate_present(
    root: Path, index: list[IndexEntry]
) -> None:
    """Refuse to seal if no failed-candidate artifact is present (spec §15
    line 443: complete negative results must be preserved before
    interpretation).

    A failed-candidate artifact is any indexed file whose path contains
    'FAILED'. If the evidence tree has windows but no FAILED artifact, the
    seal refuses (the experiment did not preserve its negative results).
    """
    has_windows = any(e.path.startswith("windows/") for e in index)
    if not has_windows:
        return
    has_failed = any("FAILED" in e.path.upper() for e in index)
    if not has_failed:
        raise ValueError(
            "failed candidate artifact missing: the evidence tree has "
            "window artifacts but no FAILED-candidate record; spec §15 "
            "line 443 requires complete negative results to be sealed "
            "before interpreting them"
        )


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _seal_to_dict(seal: SealRecord) -> dict[str, Any]:
    return {
        "root_hash": seal.root_hash,
        "algorithm": seal.algorithm,
        "signature": seal.signature,
        "public_key_fingerprint": seal.public_key_fingerprint,
        "trusted_time": seal.trusted_time,
        "receipt": seal.receipt,
        "index": [{"path": e.path, "sha256": e.sha256} for e in seal.index],
    }


__all__ = [
    "IndexEntry",
    "Publisher",
    "SealRecord",
    "Signer",
    "build_seal",
    "verify_seal",
]
