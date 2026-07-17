"""Canonical JSON hash + file-snapshot helpers for the Gold2 closed-loop proof
(Task 9, Phase 2).

Mirrors the streaming SHA-256 pattern of
``Scripts/gold2_closed_loop/lean_artifacts.py:sha256_file`` (1 MiB chunks) and
the canonical-JSON hash pattern of ``run_experiment.py:_artifact_set``.

No production code is modified.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# 1 MiB read block, matching ``lean_artifacts._HASH_CHUNK``.
_HASH_CHUNK = 1024 * 1024


def canonical_hash(obj: Any) -> str:
    """Return the SHA-256 hex of the canonical-JSON form of ``obj``.

    Canonical form: ``sort_keys=True``, ``ensure_ascii=False``,
    ``separators=(",", ":")``. This is byte-stable regardless of dict
    insertion order, so the hash is order-independent.
    """
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot_inputs(paths: list[Path] | list[str]) -> dict[str, str]:
    """Return ``{path_string: sha256_hex}`` for every file in ``paths``.

    Files are hashed in 1 MiB chunks (streaming) so multi-GB result packets
    are tractable. Missing files raise ``FileNotFoundError`` (let the caller
    decide how to handle absent snapshots).
    """
    out: dict[str, str] = {}
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            raise FileNotFoundError(f"snapshot_inputs: missing file {path}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                block = stream.read(_HASH_CHUNK)
                if not block:
                    break
                digest.update(block)
        out[str(path)] = digest.hexdigest()
    return out
