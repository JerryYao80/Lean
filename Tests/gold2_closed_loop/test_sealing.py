"""Failing contracts for the evidence seal (Task 17, Phase 4 STOP gate).

Design invariants (spec §15 lines 426-443):

* The seal INDEXES every evidence artifact: preregistration, feasibility,
  snapshots, code/config/data/assembly/model hashes, every candidate
  (including failures), every generation, every LEAN packet + trace, every
  statistic, and the verdict.
* The seal uses an INJECTED external signer (key reference) + an
  append-only publisher; it records the root hash, algorithm, public-key
  fingerprint, trusted time, and receipt.
* The seal REFUSES a missing failed-candidate artifact: a failed candidate
  MUST be present in the evidence tree before sealing (spec §15 line 443:
  "必须先封存完整负结果").
* A mutation to ANY indexed artifact after sealing breaks the seal
  (verify_seal returns False).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from Scripts.gold2_closed_loop.sealing import (
    SealRecord,
    build_seal,
    verify_seal,
)


class FakeSigner:
    """A deterministic in-memory signer for tests (production injects an
    external key reference)."""
    def __init__(self) -> None:
        self.signed: list[str] = []

    def sign(self, root_hash: str) -> tuple[str, str]:
        # Returns (signature, public_key_fingerprint).
        self.signed.append(root_hash)
        return ("sig-" + root_hash[:16], "pkfp-test")


class FakePublisher:
    """An append-only in-memory publisher (production publishes the root
    hash to an external append-only location)."""
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, record: dict[str, Any]) -> str:
        if any(p["root_hash"] == record["root_hash"] for p in self.published):
            raise RuntimeError(
                "publisher is append-only: a root hash may not be republished"
            )
        self.published.append(record)
        return "receipt-" + record["root_hash"][:16]


# --- a complete evidence tree helper ---------------------------------


def _evidence_tree(tmp_path: Path, *, with_failed: bool = True) -> Path:
    root = tmp_path / "evidence"
    root.mkdir(parents=True)
    (root / "preregistration.yaml").write_text("experiment_id: E1\n")
    (root / "feasibility").mkdir()
    (root / "feasibility" / "window_inventory.json").write_text("{}\n")
    (root / "snapshots").mkdir()
    (root / "snapshots" / "inputs.json").write_text("{}\n")
    (root / "candidate-events.jsonl").write_text(
        '{"candidate_id":"C0","event_type":"REGISTERED","execution_status":"PENDING"}\n'
        '{"candidate_id":"C1","event_type":"FAILED_STRATEGY","execution_status":"FAILED_STRATEGY"}\n'
    )
    (root / "windows").mkdir()
    (root / "windows" / "W1").mkdir()
    (root / "windows" / "W1" / "G0.json").write_text('{"packet":true}\n')
    (root / "windows" / "W1" / "trace.jsonl").write_text('{"event":"FILL"}\n')
    (root / "aggregate").mkdir()
    (root / "aggregate" / "verdict.json").write_text('{"verdict":"NOT_EFFECTIVE"}\n')
    if with_failed:
        (root / "windows" / "W1" / "FAILED-C1.json").write_text('{"failed":true}\n')
    return root


# --- anchor tests from the plan (verbatim, adapted) ------------------


def test_seal_refuses_missing_failed_candidate(tmp_path):
    tree = _evidence_tree(tmp_path, with_failed=False)
    with pytest.raises(ValueError, match="failed candidate artifact missing"):
        build_seal(tree, FakeSigner(), FakePublisher())


def test_mutation_breaks_seal(tmp_path):
    tree = _evidence_tree(tmp_path, with_failed=True)
    seal = build_seal(tree, FakeSigner(), FakePublisher())
    (tree / "aggregate" / "verdict.json").write_text("{}")
    assert verify_seal(tree, seal) is False


# --- seal record contents -------------------------------------------


def test_seal_records_root_hash_and_algorithm(tmp_path):
    tree = _evidence_tree(tmp_path, with_failed=True)
    seal = build_seal(tree, FakeSigner(), FakePublisher())
    assert seal.root_hash
    assert len(seal.root_hash) == 64
    assert seal.algorithm
    assert seal.public_key_fingerprint == "pkfp-test"
    assert seal.receipt == "receipt-" + seal.root_hash[:16]
    assert seal.trusted_time


def test_seal_indexes_every_artifact(tmp_path):
    tree = _evidence_tree(tmp_path, with_failed=True)
    seal = build_seal(tree, FakeSigner(), FakePublisher())
    indexed = {entry.path for entry in seal.index}
    expected = {
        "preregistration.yaml",
        "feasibility/window_inventory.json",
        "snapshots/inputs.json",
        "candidate-events.jsonl",
        "windows/W1/G0.json",
        "windows/W1/trace.jsonl",
        "windows/W1/FAILED-C1.json",
        "aggregate/verdict.json",
    }
    for rel in expected:
        assert any(p.endswith(rel) for p in indexed), f"missing {rel} in seal index"


def test_seal_is_stable_for_unchanged_tree(tmp_path):
    tree = _evidence_tree(tmp_path, with_failed=True)
    seal1 = build_seal(tree, FakeSigner(), FakePublisher())
    seal2 = build_seal(tree, FakeSigner(), FakePublisher())
    assert seal1.root_hash == seal2.root_hash


def test_seal_verifies_unchanged_tree(tmp_path):
    tree = _evidence_tree(tmp_path, with_failed=True)
    seal = build_seal(tree, FakeSigner(), FakePublisher())
    assert verify_seal(tree, seal) is True


def test_publisher_is_append_only(tmp_path):
    """The publisher must be append-only: republishing the same root hash
    is rejected (spec §15 line 443: external append-only location)."""
    tree = _evidence_tree(tmp_path, with_failed=True)
    pub = FakePublisher()
    build_seal(tree, FakeSigner(), pub)
    # A second publish of the SAME root hash is the append-only violation.
    with pytest.raises(RuntimeError, match="append-only"):
        build_seal(tree, FakeSigner(), pub)
    assert len(pub.published) == 1
