"""Failing contracts for atomic JSON/bytes I/O and evidence hashing helpers
(Task 9, Phase 2 STOP gate).
"""

from __future__ import annotations

import json
import os
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


# --- evidence.snapshot_inputs --------------------------------------


def test_snapshot_inputs_hashes_file(tmp_path):
    target = tmp_path / "data.bin"
    target.write_bytes(b"payload-bytes")
    result = snapshot_inputs([target])
    assert isinstance(result, dict)
    assert len(result) == 1
    digest = result[str(target)]
    assert len(digest) == 64
    # Matches a manual sha256 of the bytes.
    import hashlib

    assert digest == hashlib.sha256(b"payload-bytes").hexdigest()


def test_snapshot_inputs_multiple_files(tmp_path):
    p1 = tmp_path / "a"
    p1.write_bytes(b"aaa")
    p2 = tmp_path / "b"
    p2.write_bytes(b"bbb")
    result = snapshot_inputs([p1, p2])
    assert len(result) == 2
    assert result[str(p1)] != result[str(p2)]
