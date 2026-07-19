"""Failing contracts for the construction partition sandbox (Task 13, Phase 3).

Design invariants (spec §3 line 118, §6 line 118, §9):

* During construction the process mounts ONLY the train/review partition
  read-only snapshots; the blind partition is ABSENT from the mount list
  (``test_construction_mounts_exclude_blind``). The run directory is the
  only writable mount.
* Every mount is typed: train/review are read-only, run is writable.
* A path-whitelist + access log records every path the construction
  process touched; any access whose resolved path falls under the blind
  root is a leakage violation (``INFORMATION_LEAKAGE``).
* The blind root itself is never mounted, so a candidate builder cannot
  read blind OHLC even by accident.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from Scripts.gold2_closed_loop.partition_sandbox import (
    ConstructionSandbox,
    Mount,
    build_construction_mounts,
)


# --- anchor test from the plan (verbatim) -----------------------------


def test_construction_mounts_exclude_blind(tmp_path):
    mounts = build_construction_mounts(
        tmp_path / "train", tmp_path / "review",
        tmp_path / "blind", tmp_path / "run",
    )
    assert all("blind" not in str(m.source) for m in mounts)


# --- mount typing ------------------------------------------------------


def test_train_and_review_mounts_are_read_only(tmp_path):
    mounts = build_construction_mounts(
        tmp_path / "train", tmp_path / "review",
        tmp_path / "blind", tmp_path / "run",
    )
    by_name = {m.name: m for m in mounts}
    assert by_name["train"].read_only is True
    assert by_name["review"].read_only is True


def test_run_mount_is_writable_and_present(tmp_path):
    mounts = build_construction_mounts(
        tmp_path / "train", tmp_path / "review",
        tmp_path / "blind", tmp_path / "run",
    )
    by_name = {m.name: m for m in mounts}
    assert "run" in by_name
    assert by_name["run"].read_only is False


def test_blind_root_is_not_among_mounts(tmp_path):
    mounts = build_construction_mounts(
        tmp_path / "train", tmp_path / "review",
        tmp_path / "blind", tmp_path / "run",
    )
    assert "blind" not in {m.name for m in mounts}


# --- sandbox access logging + blind-access detection ------------------


def test_sandbox_records_access_in_log(tmp_path):
    train = tmp_path / "train"; train.mkdir()
    review = tmp_path / "review"; review.mkdir()
    blind = tmp_path / "blind"; blind.mkdir()
    run = tmp_path / "run"; run.mkdir()
    sandbox = ConstructionSandbox.from_roots(
        train=train, review=review, blind=blind, run=run,
        access_log=tmp_path / "access.jsonl",
    )
    sandbox.record_access(train / "518880.csv")
    violations = sandbox.validate_no_blind_access()
    assert violations == []
    assert (tmp_path / "access.jsonl").is_file()


def test_sandbox_flags_blind_access_as_leakage(tmp_path):
    train = tmp_path / "train"; train.mkdir()
    review = tmp_path / "review"; review.mkdir()
    blind = tmp_path / "blind"; blind.mkdir()
    run = tmp_path / "run"; run.mkdir()
    sandbox = ConstructionSandbox.from_roots(
        train=train, review=review, blind=blind, run=run,
        access_log=tmp_path / "access.jsonl",
    )
    sandbox.record_access(blind / "2022.csv")
    violations = sandbox.validate_no_blind_access()
    assert len(violations) == 1
    assert violations[0].invalid_reason == "INFORMATION_LEAKAGE"
    assert "blind" in violations[0].detail


def test_sandbox_allowed_roots_exclude_blind(tmp_path):
    train = tmp_path / "train"; train.mkdir()
    review = tmp_path / "review"; review.mkdir()
    blind = tmp_path / "blind"; blind.mkdir()
    run = tmp_path / "run"; run.mkdir()
    sandbox = ConstructionSandbox.from_roots(
        train=train, review=review, blind=blind, run=run,
        access_log=tmp_path / "access.jsonl",
    )
    allowed = [Path(r) for r in sandbox.allowed_roots]
    assert all(not _is_relative_to(blind.resolve(), Path(r).resolve())
               for r in allowed)
    assert any(_is_relative_to(train.resolve(), Path(r).resolve()) for r in allowed)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
