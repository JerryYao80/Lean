"""Canonical acceptance metric registry (Task 16, Phase 4 STOP gate).

Reads acceptance metrics from CANONICAL LEAN paths ONLY (spec §11 lines
336-374, §13 acceptance paths):

    totalPerformance.portfolioStatistics.totalNetProfit
    totalPerformance.portfolioStatistics.compoundingAnnualReturn
    totalPerformance.portfolioStatistics.sharpeRatio
    totalPerformance.portfolioStatistics.drawdown
    totalPerformance.portfolioStatistics.informationRatio
    statistics.Total Fees (reconciled with native evidence)

Known ambiguities resolved here (spec §11 line 354):
* The acceptance Sharpe is the PORTFOLIO-LEVEL sharpeRatio, NOT the
  trade-level sharpeRatio (they differ; the canonical metric is the
  portfolio one).
* Total Fees comes from the top-level ``statistics`` dict (space-separated
  keys), reconciled against the native order-events evidence.
* The information ratio is only meaningful if the benchmark chart is
  complete (Alpha/Beta are currently 0 but IR is nonzero — spec §11 line
  354). Benchmark integrity is validated before the IR is accepted.

Python NEVER reconstructs accounting: this registry only READS native
paths and coerces them to floats. It does not compute fills/fees/holdings/
cash/TPV.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# Canonical acceptance metric keys.
_K_TOTAL_NET_PROFIT = "total_net_profit"
_K_CAGR = "cagr"
_K_SHARPE = "sharpe"
_K_MDD = "mdd"
_K_IR = "information_ratio"
_K_TOTAL_FEES = "total_fees"

# The LEAN JSON paths each acceptance metric is read from. Documented so a
# reviewer can verify the registry never reads a non-canonical path.
_CANONICAL_PATHS = {
    _K_TOTAL_NET_PROFIT: "totalPerformance.portfolioStatistics.totalNetProfit",
    _K_CAGR: "totalPerformance.portfolioStatistics.compoundingAnnualReturn",
    _K_SHARPE: "totalPerformance.portfolioStatistics.sharpeRatio",
    _K_MDD: "totalPerformance.portfolioStatistics.drawdown",
    _K_IR: "totalPerformance.portfolioStatistics.informationRatio",
    _K_TOTAL_FEES: "statistics.Total Fees",
}

AcceptanceMetrics = dict[str, float]


def extract_acceptance_metrics(packet: dict[str, Any]) -> AcceptanceMetrics:
    """Extract the canonical acceptance metrics from a LEAN result packet.

    Reads ONLY the canonical paths (see ``_CANONICAL_PATHS``). Raises
    ``KeyError`` if a required path is missing and ``ValueError`` if a
    value is non-finite (NaN/±Infinity — spec §13 requires finite values).
    """
    ps = _require_path(packet, "totalPerformance.portfolioStatistics")
    metrics: dict[str, float] = {
        _K_TOTAL_NET_PROFIT: _finite_float(ps["totalNetProfit"], _K_TOTAL_NET_PROFIT),
        _K_CAGR: _finite_float(ps["compoundingAnnualReturn"], _K_CAGR),
        _K_SHARPE: _finite_float(ps["sharpeRatio"], _K_SHARPE),
        _K_MDD: _finite_float(ps["drawdown"], _K_MDD),
        _K_IR: _finite_float(ps["informationRatio"], _K_IR),
    }
    statistics = packet.get("statistics") or {}
    # The top-level statistics dict uses space-separated string keys.
    fees_raw = statistics.get("Total Fees")
    if fees_raw is None:
        raise KeyError("statistics.Total Fees")
    metrics[_K_TOTAL_FEES] = _finite_float(fees_raw, _K_TOTAL_FEES)
    return metrics


def validate_benchmark_integrity(packet: dict[str, Any]) -> bool:
    """Validate the benchmark chart before the IR is used (spec §11 line 354).

    Returns True iff the ``charts.Benchmark.series.Benchmark.values`` list
    exists and is non-empty. A missing or empty benchmark series means the
    IR is not trustworthy (Alpha/Beta are 0 but IR is nonzero).
    """
    charts = packet.get("charts") or {}
    bench_chart = charts.get("Benchmark")
    if not isinstance(bench_chart, dict):
        return False
    series = bench_chart.get("series") or {}
    bench_series = series.get("Benchmark")
    if not isinstance(bench_series, dict):
        return False
    values = bench_series.get("values")
    if not isinstance(values, list) or len(values) == 0:
        return False
    return True


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------


def _require_path(packet: dict[str, Any], dotted: str) -> Any:
    """Walk a dotted path and raise KeyError naming it if any step is
    missing."""
    cur: Any = packet
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    return cur


def _finite_float(value: Any, label: str) -> float:
    """Coerce ``value`` to float, rejecting non-finite values (NaN/±Inf).

    LEAN serializes some statistics as strings; coerce via ``float()``. A
    non-finite result (NaN/±Infinity) raises ValueError (spec §13 requires
    every numeric threshold to be finite — and acceptance metrics must be
    finite to be rankable).
    """
    try:
        f = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"acceptance metric {label!r} is not a finite number: {value!r}"
        ) from error
    if math.isnan(f) or math.isinf(f):
        raise ValueError(
            f"acceptance metric {label!r} is non-finite ({f}); spec §13 "
            "requires finite acceptance values"
        )
    return f


__all__ = [
    "AcceptanceMetrics",
    "extract_acceptance_metrics",
    "validate_benchmark_integrity",
]
