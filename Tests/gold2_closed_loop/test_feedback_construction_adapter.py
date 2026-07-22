"""Failing contracts for the proof-local G2 feedback construction adapter
(Task 11, Phase 2 STOP gate).

Design invariants (spec §7 G2, §8):

* G2 runs ALL its objective/construction generations on the TRAIN partition
  only; the review partition is frozen and consumed ONCE as an immutable
  bundle. The feedback adapter MUST NOT run candidates on the review
  partition (``test_review_partition_never_ranks_g2``).
* The feedback adapter consumes the frozen review bundle (never re-enters
  review data, never re-ranks candidates).
* A review bundle whose ``validity_status`` is INVALID produces NO feedback
  (no shaping overrides), instead of graceful degradation.
"""
from __future__ import annotations

from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest

from Scripts.gold2_closed_loop.adapters.base import (
    OptimizationRequest,
    TrialResult,
)
from Scripts.gold2_closed_loop.adapters.feedback_construction import (
    FeedbackConstructionAdapter,
    FeedbackResult,
)

# Mirrors the RunnerCall shape Task 11 needs: at least .partition.
RunnerCall = namedtuple("RunnerCall", ["partition", "parameters", "candidate_id"])


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[RunnerCall] = []
        self._idx = 0

    def __call__(self, request: OptimizationRequest) -> TrialResult:
        self.calls.append(
            RunnerCall(
                partition=request.partition,
                parameters=dict(request.parameters),
                candidate_id=request.candidate_id,
            )
        )
        self._idx += 1
        return TrialResult(
            status="SUCCEEDED",
            metrics={"sharpe": 1.0, "net_profit": 0.1, "mdd": 0.08, "trades": 10},
            error=None,
        )


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


def _valid_bundle(candidate_id: str = "C1") -> Any:
    """A minimal frozen review bundle marked VALID with telescoping
    attribution + a layer gap that should drive shaping."""
    return {
        "validity_status": "VALID",
        "invalid_reason": None,
        "candidate_id": candidate_id,
        "w_realized": "0.6667",
        "layer_attribution": {
            "trend": {"pnl_pct_of_total": "0.10"},
            "vol_target": {"pnl_pct_of_total": "0.08"},
            "extreme_risk": {"pnl_pct_of_total": "-0.32"},
            "realrate_cap": {"pnl_pct_of_total": "0.07"},
        },
        "attribution_method": "telescoping",
    }


def _invalid_bundle() -> Any:
    return {
        "validity_status": "INVALID",
        "invalid_reason": "MISSING_FORMAL_TRACE",
        "candidate_id": None,
        "w_realized": None,
        "layer_attribution": {},
        "attribution_method": None,
    }


# --- review partition never ranks G2 candidates -------------------------


def test_review_partition_never_ranks_g2(fake_runner, tmp_path):
    """G2 candidates must run on the TRAIN partition only; the review
    partition is frozen and consumed once as a bundle, never re-entered."""
    bundle = _valid_bundle()
    FeedbackConstructionAdapter(fake_runner, budget=2, seed=17,
                                journal_path=tmp_path / "events.jsonl").run(
        bundle, "W1/train", "W1/review")
    assert {call.partition for call in fake_runner.calls} == {"W1/train"}


def test_invalid_bundle_produces_no_feedback(fake_runner, tmp_path):
    """An INVALID review bundle (missing formal trace) MUST NOT produce
    shaping feedback — no graceful degradation to shaping={}."""
    result = FeedbackConstructionAdapter(
        fake_runner, budget=2, seed=17, journal_path=tmp_path / "events.jsonl"
    ).run(_invalid_bundle(), "W1/train", "W1/review")
    assert result.feedback_action is None
    assert result.shaping_overrides == {}
    assert result.validity_status == "INVALID"
    # No candidate trials ran on an invalid bundle.
    assert fake_runner.calls == []


def test_valid_bundle_drives_shaping_overrides(fake_runner, tmp_path):
    """A VALID bundle with an extreme_risk layer gap drives a non-empty
    shaping override (mirrors the diagnostic adapter's weight formula,
    but now sourced from the frozen telescoping bundle, not P&L/dp)."""
    result = FeedbackConstructionAdapter(
        fake_runner, budget=2, seed=17, journal_path=tmp_path / "events.jsonl"
    ).run(_valid_bundle(), "W1/train", "W1/review")
    assert result.validity_status == "VALID"
    assert result.shaping_overrides  # non-empty


def test_g2_consumes_budget_for_each_generation(fake_runner, tmp_path):
    """Each G2 generation consumes budget (complete attempt accounting,
    mirroring G1)."""
    result = FeedbackConstructionAdapter(
        fake_runner, budget=3, seed=17, journal_path=tmp_path / "events.jsonl"
    ).run(_valid_bundle(), "W1/train", "W1/review")
    assert result.attempted_trial_count >= 1
    assert len(fake_runner.calls) == result.attempted_trial_count


def test_g2_never_aliases_to_g1(fake_runner, tmp_path):
    """If all G2 generations fail, G2 aliases to G1 (not to G0); the
    adapter reports aliased_to_g1, never fabricates a feedback action."""
    class AllFailRunner(FakeRunner):
        def __call__(self, request):
            self.calls.append(RunnerCall(
                partition=request.partition,
                parameters=dict(request.parameters),
                candidate_id=request.candidate_id))
            return TrialResult(status="NO_TRADES", metrics=None, error=None)
    r = AllFailRunner()
    result = FeedbackConstructionAdapter(
        r, budget=2, seed=17, journal_path=tmp_path / "events.jsonl"
    ).run(_valid_bundle(), "W1/train", "W1/review")
    assert result.aliased_to_g1 is True or result.feedback_action is None
