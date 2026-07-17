"""Failing contracts for atomic JSON/bytes I/O and evidence hashing helpers
(Task 9, Phase 2 STOP gate).
"""

from __future__ import annotations

import json
import os
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from Scripts.gold2_closed_loop.atomic_io import (
    _fsync_directory,
    atomic_write_bytes,
    atomic_write_json,
)
from Scripts.gold2_closed_loop.evidence import canonical_hash, snapshot_inputs


class Boom(Exception):
    """Marker exception for testing the atomic-write cleanup path."""


# --- atomic_write_json ---------------------------------------------


def test_atomic_write_json_writes_parsable(tmp_path):
    path = tmp_path / "out.json"
    obj = {"b": 2, "a": 1, "nested": {"z": [3, 2, 1]}}
    atomic_write_json(path, obj)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == obj


def test_atomic_write_json_sorted_keys(tmp_path):
    path = tmp_path / "out.json"
    atomic_write_json(path, {"z": 1, "a": 2})
    raw = path.read_text(encoding="utf-8")
    # sorted keys: "a" must appear before "z"
    assert raw.index('"a"') < raw.index('"z"')


def test_atomic_write_json_preserves_existing_on_error(tmp_path):
    path = tmp_path / "out.json"
    path.write_text('{"existing": true}', encoding="utf-8")

    def boom_writer(fd: int, data: bytes) -> None:
        raise Boom("simulated write failure")

    with pytest.raises(Boom):
        atomic_write_json(path, {"will": "fail"}, _writer=boom_writer)
    # Existing content survives the failure.
    assert json.loads(path.read_text(encoding="utf-8")) == {"existing": True}
    # No leftover temp files in the parent dir.
    leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".out.json.")]
    assert leftover == []


def test_atomic_write_json_no_tmp_left_behind(tmp_path):
    path = tmp_path / "out.json"
    atomic_write_json(path, {"x": 1})
    # No leftover temp files (prefix ".out.json.") in the parent dir.
    leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".out.json.")]
    assert leftover == []


# --- atomic_write_bytes --------------------------------------------


def test_atomic_write_bytes_roundtrip(tmp_path):
    path = tmp_path / "blob.bin"
    payload = b"\x00\x01\x02hello"
    atomic_write_bytes(path, payload)
    assert path.read_bytes() == payload


def test_atomic_write_bytes_preserves_existing_on_error(tmp_path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"original")

    def boom_writer(fd: int, data: bytes) -> None:
        raise Boom("simulated write failure")

    with pytest.raises(Boom):
        atomic_write_bytes(path, b"new", _writer=boom_writer)
    assert path.read_bytes() == b"original"
    leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".blob.bin.")]
    assert leftover == []


# --- _fsync_directory ----------------------------------------------


def test_fsync_directory_callable(tmp_path):
    # Just must not raise.
    _fsync_directory(tmp_path)


# --- evidence.canonical_hash ---------------------------------------


def test_canonical_hash_order_independent():
    a = canonical_hash({"a": 1, "b": 2})
    b = canonical_hash({"b": 2, "a": 1})
    assert a == b
    assert len(a) == 64


def test_canonical_hash_differs_on_value():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_canonical_hash_differs_on_whitespace_in_string():
    # Strings are preserved exactly; whitespace inside a value matters.
    assert canonical_hash({"a": "x y"}) != canonical_hash({"a": "xy"})


# --- evidence.canonical_hash: default serializer (Minor 7) ------------


def test_canonical_hash_serializes_decimal_as_exact_text():
    # Decimal must be preserved as exact decimal text, NOT float-rounded.
    a = canonical_hash({"x": Decimal("0.1")})
    b = canonical_hash({"x": "0.1"})
    assert a == b
    # A float-rounded 0.1 would NOT equal the decimal text "0.1".
    assert canonical_hash({"x": float("0.1")}) != canonical_hash({"x": "0.1"})


def test_canonical_hash_serializes_path_as_string():
    a = canonical_hash({"p": Path("/tmp/x")})
    b = canonical_hash({"p": "/tmp/x"})
    assert a == b


def test_canonical_hash_serializes_datetime_and_date_as_iso():
    dt = datetime(2026, 7, 14, 3, 0, 0, tzinfo=timezone.utc)
    # datetime -> isoformat(); the +00:00 suffix is preserved verbatim
    # (callers that want a trailing Z should pass a string themselves).
    a = canonical_hash({"t": dt})
    b = canonical_hash({"t": dt.isoformat()})
    assert a == b
    d = date(2026, 7, 14)
    a2 = canonical_hash({"d": d})
    b2 = canonical_hash({"d": d.isoformat()})
    assert a2 == b2


def test_canonical_hash_rejects_unknown_type_with_clear_error():
    class Weird:
        pass
    with pytest.raises(TypeError, match="canonical_hash"):
        canonical_hash({"x": Weird()})


def test_canonical_hash_nfc_normalizes_strings():
    # Decomposed and precomposed forms of "e-acute" must hash identically (NIT 8).
    precomposed = "caf\u00e9"  # single-codepoint e-acute (U+00E9)
    decomposed = unicodedata.normalize("NFD", precomposed)  # 'e' + combining acute
    # Sanity: the raw unicode strings differ before NFC.
    assert decomposed != precomposed
    # After NFC inside canonical_hash they must hash identically.
    assert canonical_hash({"name": decomposed}) == canonical_hash({"name": precomposed})