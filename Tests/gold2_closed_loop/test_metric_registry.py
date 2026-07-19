"""Failing contracts for the canonical metric registry (Task 16, Phase 4).

Design invariants (spec §11 lines 336-374, §13 acceptance paths):

* Acceptance metrics come from CANONICAL LEAN paths only:
    totalPerformance.portfolioStatistics.totalNetProfit
    totalPerformance.portfolioStatistics.compoundingAnnualReturn
    totalPerformance.portfolioStatistics.sharpeRatio
    totalPerformance.portfolioStatistics.drawdown
    totalPerformance.portfolioStatistics.informationRatio
    statistics.Total Fees (reconciled with native evidence)
* The Sharpe ratio MUST come from portfolioStatistics, NOT trade-level
  sharpeRatio (they differ; the canonical acceptance metric is the
  portfolio-level one).
* Benchmark chart integrity must be validated BEFORE the information
  ratio is used (Alpha/Beta are currently 0 but IR is nonzero — the IR
  is only meaningful if the benchmark series is complete).
* Python NEVER reconstructs accounting (fills/fees/holdings/cash/TPV):
  the registry only READS native paths; it does not compute them.
"""
from __future__ import annotations

from typing import Any

import pytest

from Scripts.gold2_closed_loop.metric_registry import (
    AcceptanceMetrics,
    extract_acceptance_metrics,
    validate_benchmark_integrity,
)


def _packet(sharpe_portfolio: float = 0.6,
            sharpe_trade: float = 9.0,
            total_fees: float = 100.0,
            benchmark_points: int = 252,
            ) -> dict[str, Any]:
    return {
        "totalPerformance": {
            "portfolioStatistics": {
                "totalNetProfit": 12345.0,
                "compoundingAnnualReturn": 0.12,
                "sharpeRatio": sharpe_portfolio,
                "drawdown": 0.08,
                "informationRatio": 0.15,
            },
            "tradeStatistics": {
                "sharpeRatio": sharpe_trade,
            },
        },
        "statistics": {
            "Total Fees": str(total_fees),
        },
        "charts": {
            "Benchmark": {
                "series": {
                    "Benchmark": {
                        "values": [{"x": i, "y": float(i)} for i in range(benchmark_points)],
                    }
                }
            }
        },
    }


# --- anchor test from the plan (verbatim) ----------------------------


def test_sharpe_comes_from_portfolio_statistics(packet):
    packet["totalPerformance"]["portfolioStatistics"]["sharpeRatio"] = .6
    packet["totalPerformance"]["tradeStatistics"]["sharpeRatio"] = 9.0
    assert extract_acceptance_metrics(packet)["sharpe"] == .6


@pytest.fixture
def packet() -> dict[str, Any]:
    return _packet()


# --- canonical paths -------------------------------------------------


def test_total_net_profit_from_portfolio_statistics(packet):
    m = extract_acceptance_metrics(packet)
    assert m["total_net_profit"] == 12345.0


def test_cagr_from_portfolio_statistics(packet):
    m = extract_acceptance_metrics(packet)
    assert m["cagr"] == 0.12


def test_drawdown_from_portfolio_statistics(packet):
    m = extract_acceptance_metrics(packet)
    assert m["mdd"] == 0.08


def test_information_ratio_from_portfolio_statistics(packet):
    m = extract_acceptance_metrics(packet)
    assert m["information_ratio"] == 0.15


def test_total_fees_from_statistics_reconciled(packet):
    m = extract_acceptance_metrics(packet)
    assert m["total_fees"] == 100.0


def test_missing_portfolio_statistics_raises(packet):
    del packet["totalPerformance"]["portfolioStatistics"]
    with pytest.raises((KeyError, ValueError)):
        extract_acceptance_metrics(packet)


def test_non_finite_sharpe_raises(packet):
    packet["totalPerformance"]["portfolioStatistics"]["sharpeRatio"] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        extract_acceptance_metrics(packet)


# --- benchmark integrity before IR -----------------------------------


def test_benchmark_integrity_passes_when_complete(packet):
    assert validate_benchmark_integrity(packet) is True


def test_benchmark_integrity_fails_when_empty(packet):
    packet["charts"]["Benchmark"]["series"]["Benchmark"]["values"] = []
    assert validate_benchmark_integrity(packet) is False


def test_benchmark_integrity_fails_when_missing(packet):
    del packet["charts"]["Benchmark"]
    assert validate_benchmark_integrity(packet) is False


def test_information_ratio_gate_requires_benchmark_integrity(packet):
    """If the benchmark series is incomplete, the IR is not trustworthy;
    the registry must flag the gate as failed before IR is accepted."""
    packet["charts"]["Benchmark"]["series"]["Benchmark"]["values"] = []
    assert validate_benchmark_integrity(packet) is False
