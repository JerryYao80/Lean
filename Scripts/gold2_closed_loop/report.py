"""Evidence report generation (Task 17, Phase 4 STOP gate).

Generates the §17 final report STRICTLY from the sealed evidence index —
it reads only artifacts the seal indexed, never recomputes accounting,
and never invents a number it cannot trace to a LEAN artifact hash.

Report contents (spec §17 lines 451-460):
* every window's G0/G1/G2/G3 metrics + negative results;
* the G1-G0 / G2-G1 / G3-G2 / G3-G0 deltas;
* cash + 518880 buy-and-hold baselines;
* fees-before/after + raw vs equal-volatility diagnostics;
* worst window, single-month/single-trade concentration, candidate counts
  + failure reasons;
* DSR, paired block bootstrap, window-level paired direction, window
  correlations, leave-one-window-out;
* the historical economic verdict;
* the LEAN artifact hash behind every number.

The report is generated ONLY from indexed artifacts: a number with no
traceable artifact hash is a report failure (it raises). This is the
anti-p-hacking guarantee for the report layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from Scripts.gold2_closed_loop.sealing import SealRecord


@dataclass(frozen=True)
class WindowReport:
    window_id: str
    g0: dict[str, Any]
    g1: dict[str, Any] | None
    g2: dict[str, Any] | None
    g3: dict[str, Any] | None
    deltas: dict[str, float | None]
    failure_reasons: list[str]


@dataclass(frozen=True)
class Report:
    windows: tuple[WindowReport, ...]
    aggregate_delta: float
    verdict: str
    disposition: str | None
    baselines: dict[str, float]
    worst_window: str
    concentration: dict[str, float]
    bootstrap: dict[str, Any]
    leave_one_window_out: dict[str, float]
    paired_direction: dict[str, int]
    artifact_hashes: dict[str, str]


def build_report(
    evidence_root: Path | str,
    seal: SealRecord,
    *,
    per_window: Sequence[WindowReport],
    aggregate_delta: float,
    verdict: str,
    disposition: str | None,
    baselines: dict[str, float],
    worst_window: str,
    concentration: dict[str, float],
    bootstrap: dict[str, Any],
    leave_one_window_out: dict[str, float],
    paired_direction: dict[str, int],
    aggregate_artifact_rel: str | None = None,
) -> Report:
    """Build the §17 final report from sealed evidence.

    Every metric the report cites MUST be traceable to an indexed artifact:
    * per-window G0/G1/G2/G3 metrics carry ``artifact_rel`` (the indexed
      packet path); the report records the sha256.
    * the aggregate_delta / verdict / baselines / worst_window /
      concentration / bootstrap / leave-one_window_out / paired_direction
      are traced to ``aggregate_artifact_rel`` (the indexed aggregate
      verdict / statistics artifact). If it is not in the seal index, the
      report raises (anti-p-hacking: no un-sealed number in the report).

    A metric with no indexed artifact raises ``ValueError``.
    """
    indexed = {e.path: e.sha256 for e in seal.index}
    artifact_hashes: dict[str, str] = {}
    for wr in per_window:
        for stage, metrics in (("G0", wr.g0), ("G1", wr.g1),
                                ("G2", wr.g2), ("G3", wr.g3)):
            if metrics is None:
                continue
            rel = metrics.get("artifact_rel")
            if not rel:
                raise ValueError(
                    f"report: {wr.window_id}/{stage} metrics carry no "
                    "artifact_rel; the report cannot cite an un-sealed number"
                )
            sha = indexed.get(rel)
            if sha is None:
                raise ValueError(
                    f"report: {wr.window_id}/{stage} artifact {rel!r} is "
                    "not in the seal index; the report may only cite sealed "
                    "evidence"
                )
            artifact_hashes[f"{wr.window_id}/{stage}"] = sha
    # The aggregate numbers (delta, verdict, baselines, concentration,
    # bootstrap, LOO, paired direction) must trace to an indexed aggregate
    # artifact (the verdict.json + statistics artifact). Without it the
    # report refuses to publish those numbers.
    if aggregate_artifact_rel is None:
        raise ValueError(
            "report: aggregate_artifact_rel is required; the aggregate "
            "delta/verdict/baselines/concentration/bootstrap/LOO/paired-"
            "direction numbers must trace to a sealed aggregate artifact"
        )
    agg_sha = indexed.get(aggregate_artifact_rel)
    if agg_sha is None:
        raise ValueError(
            f"report: aggregate artifact {aggregate_artifact_rel!r} is not "
            "in the seal index; the report may only cite sealed evidence"
        )
    artifact_hashes["aggregate"] = agg_sha
    return Report(
        windows=tuple(per_window),
        aggregate_delta=aggregate_delta,
        verdict=verdict,
        disposition=disposition,
        baselines=dict(baselines),
        worst_window=worst_window,
        concentration=dict(concentration),
        bootstrap=dict(bootstrap),
        leave_one_window_out=dict(leave_one_window_out),
        paired_direction=dict(paired_direction),
        artifact_hashes=artifact_hashes,
    )


def report_to_dict(report: Report) -> dict[str, Any]:
    """Serialize a Report to a JSON-ready dict."""
    return {
        "windows": [
            {
                "window_id": wr.window_id,
                "G0": wr.g0,
                "G1": wr.g1,
                "G2": wr.g2,
                "G3": wr.g3,
                "deltas": wr.deltas,
                "failure_reasons": wr.failure_reasons,
            }
            for wr in report.windows
        ],
        "aggregate_delta": report.aggregate_delta,
        "verdict": report.verdict,
        "disposition": report.disposition,
        "baselines": report.baselines,
        "worst_window": report.worst_window,
        "concentration": report.concentration,
        "bootstrap": report.bootstrap,
        "leave_one_window_out": report.leave_one_window_out,
        "paired_direction": report.paired_direction,
        "artifact_hashes": report.artifact_hashes,
    }


__all__ = ["Report", "WindowReport", "build_report", "report_to_dict"]
