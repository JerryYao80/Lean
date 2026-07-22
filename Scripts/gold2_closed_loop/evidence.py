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
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# 1 MiB read block, matching ``lean_artifacts._HASH_CHUNK``.
_HASH_CHUNK = 1024 * 1024


def _canonical_default(obj: Any) -> str:
    """``json.dumps(default=...)`` serializer for ``canonical_hash``.

    Coerces types that ``json`` cannot natively serialize into a stable string
    form so the canonical hash is well-defined for evidence records that carry
    ``Decimal`` thresholds, ``Path`` snapshots, or ``datetime`` timestamps.

    * ``Decimal`` -> ``str(value)`` (preserves the exact decimal text; do NOT
      route through ``float`` because that would round 0.1 to an inexact
      binary value and break hash stability across representations).
    * ``Path``    -> ``str(value)``.
    * ``datetime``/``date`` -> ``value.isoformat()`` (UTC callers should
      ensure tz-awareness before hashing).
    * ``str``     -> NFC-normalized via ``unicodedata.normalize("NFC", ...)``.
      NOTE: ``json.dumps`` only invokes ``default`` for NON-JSON-native
      values, so this branch is defensive — ordinary ``str`` values in the
      tree are emitted by the encoder itself. NFC normalization for ordinary
      strings is applied to the FINAL encoded text in ``canonical_hash``
      (single normalization point), so precomposed and decomposed forms of
      the same logical string hash identically regardless of the path the
      bytes take through the encoder.

    Raises
    ------
    TypeError:
        For any type this serializer cannot coerce. The message names
        ``canonical_hash`` and the offending type so the failure is
        actionable rather than the opaque ``TypeError: Object of type X is
        not JSON serializable`` that ``json.dumps`` would otherwise raise.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, str):
        # Defensive: json only calls default for non-native types, but if a
        # subclass routes str through here we still normalize.
        return unicodedata.normalize("NFC", obj)
    raise TypeError(
        f"canonical_hash: cannot serialize value of type "
        f"{type(obj).__name__!r} (value={obj!r}); coerce it to a JSON-native "
        "type (str/int/float/bool/None/dict/list) or extend _canonical_default"
    )


def canonical_hash(obj: Any) -> str:
    """Return the SHA-256 hex of the canonical-JSON form of ``obj``.

    Canonical form: ``sort_keys=True``, ``ensure_ascii=False``,
    ``separators=(",", ":")``. This is byte-stable regardless of dict
    insertion order, so the hash is order-independent.

    The ``default=`` serializer coerces ``Decimal`` / ``Path`` /
    ``datetime`` / ``date`` to canonical strings (see ``_canonical_default``)
    so callers may pass rich python objects without an opaque ``TypeError``.

    NFC unicode normalization is applied to the FINAL encoded JSON text so
    precomposed and decomposed forms of the same logical string hash
    identically (e.g. ``café`` as U+00E9 vs ``cafe`` + U+0301). This is the
    single normalization point for the hash; callers do not need to
    pre-normalize.
    """
    text = json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_canonical_default,
    )
    # NFC the whole encoded text: this collapses every combining sequence
    # (whether it came from a str value, a str key, or a coerced non-native
    # value) into a single canonical form so the hash is stable across
    # unicode normalization forms. Operating on the final text is the only
    # place that covers ALL str inputs, because ``json.dumps`` never invokes
    # ``default`` for native str values.
    text = unicodedata.normalize("NFC", text)
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
