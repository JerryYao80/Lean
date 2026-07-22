"""Integration-gated P2 construction harness (real LEAN + real 518880 data).

Wires the proof adapters end-to-end across W1-W4:
  G0 (proof strategy, frozen defaults) -> formal review (frozen bundle) ->
  G1 (parameter search over train) -> G2 (feedback construction on train).

Each LEAN run is real (dotnet Launcher). Metrics come LEAN-native from the
result packet's totalPerformance.portfolioStatistics + tradeStatistics +
order-events sidecar. w_realized comes from the formal trace HOLDINGS_SNAPSHOT.

Emits a STOP P2 verdict: >=3 valid formal G0/G1/G2 windows => PASS.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from Scripts.gold2_closed_loop.lean_runner import build_run_config, run_lean
from Scripts.gold2_closed_loop.adapters.base import OptimizationRequest, TrialResult
from Scripts.gold2_closed_loop.adapters.formal_review import FormalReviewAdapter
from Scripts.gold2_closed_loop.adapters.feedback_construction import (
    FeedbackConstructionAdapter,
)
from Scripts.gold2_closed_loop.phase0_types import proof_windows

DLL = ROOT / "Algorithm.CSharp" / "bin" / "Debug" / "QuantConnect.Algorithm.CSharp.dll"
BASE = json.loads(
    (ROOT / "Scripts/gold2_closed_loop/config/proof_lean_base.json").read_text()
)
BASE["algorithm-location"] = str(DLL.resolve())


def _lean_run(run_dir, run_id, stage_id, candidate_id, window_id,
              start, end, trace_path, params_override=None):
    params = dict(BASE.get("parameters", {}))
    if params_override:
        params.update({k: str(v) for k, v in params_override.items()})
    params["start-date"] = start
    params["end-date"] = end
    cfg = build_run_config(
        BASE, run_dir, "Gold2ClosedLoopProofStrategy", params,
        run_id=run_id, start_date=start, end_date=end, trace_path=trace_path,
        experiment_id="E1", window_id=window_id, stage_id=stage_id,
        candidate_id=candidate_id,
    )
    return run_lean(cfg, run_dir=run_dir, run_id=run_id, timeout_seconds=600,
                    worktree_root=ROOT, trace_path=trace_path)


def _metrics(run_dir, run_id):
    pkt = json.loads((run_dir / f"{run_id}.json").read_text())
    ps = pkt["totalPerformance"]["portfolioStatistics"]
    oe = json.loads((run_dir / f"{run_id}-order-events.json").read_text())
    fills = [e for e in oe if e.get("status") == "filled"]
    fee = sum(float(e.get("orderFeeAmount") or 0.0) for e in fills)
    return {
        "sharpe": float(ps.get("sharpeRatio", 0.0)),
        "net_profit": float(ps.get("totalNetProfit", 0.0)),
        "mdd": float(ps.get("drawdown", 0.0)),
        "trades": len(fills),
        "dsr": float(ps.get("sortinoRatio", 0.0)),
        "total_fees": fee,
        "end_equity": float(ps.get("endEquity", 0.0)),
    }


def _iso(d):
    return d.isoformat()


def main():
    out_root = ROOT / "result" / "gold2-p2-construction"
    out_root.mkdir(parents=True, exist_ok=True)
    windows = proof_windows()
    report = {"windows": []}

    for w in windows:
        wid = w.window_id
        wdir = out_root / wid
        wdir.mkdir(parents=True, exist_ok=True)
        g0_dir = wdir / "G0"; g0_dir.mkdir()
        g0_trace = g0_dir / "trace.jsonl"
        g0 = _lean_run(g0_dir, f"{wid}-G0-C0", "G0", "C0", wid,
                       _iso(w.train[0]), _iso(w.train[1]), g0_trace)
        g0m = _metrics(g0_dir, f"{wid}-G0-C0")
        review = FormalReviewAdapter().run(g0_trace, bundle_dir=wdir / "review")

        def g1_runner(req: OptimizationRequest) -> TrialResult:
            cdir = wdir / "G1" / req.candidate_id; cdir.mkdir(parents=True, exist_ok=True)
            ctr = cdir / "trace.jsonl"
            p = req.parameters
            ov = {}
            if "trend-ma-short" in p: ov["trend-ma-short"] = p["trend-ma-short"]
            if "vol-target" in p: ov["vol-target"] = p["vol-target"]
            r = _lean_run(cdir, f"{wid}-G1-{req.candidate_id}", "G1",
                          req.candidate_id, wid, _iso(w.train[0]),
                          _iso(w.train[1]), ctr, params_override=ov)
            if not r.is_success():
                return TrialResult(status="FAILED_STRATEGY", metrics=None, error=r.error)
            return TrialResult(status="SUCCEEDED",
                               metrics=_metrics(cdir, f"{wid}-G1-{req.candidate_id}"),
                               error=None)

        from Scripts.gold2_closed_loop.adapters.parameter_optimizer import (
            ParameterOptimizerAdapter,
        )
        g1 = ParameterOptimizerAdapter(
            g1_runner, budget=4, seed=17, journal_path=wdir / "g1-events.jsonl",
            stage_id="G1", window_id=wid,
        ).run(
            {"trend-ma-short": {"type": "choice", "values": ["20", "30"]},
             "vol-target": {"type": "choice", "values": ["0.11", "0.15"]}},
            f"{wid}/train",
        )

        bundle = {
            "validity_status": review.validity_status,
            "invalid_reason": review.invalid_reason,
            "candidate_id": review.bundle.candidate_id if review.bundle else None,
            "layer_attribution": (review.bundle.layer_attribution
                                  if review.bundle else {}),
            "attribution_method": (review.bundle.attribution_method
                                   if review.bundle else None),
        }

        def g2_runner(req: OptimizationRequest) -> TrialResult:
            cdir = wdir / "G2" / req.candidate_id; cdir.mkdir(parents=True, exist_ok=True)
            ctr = cdir / "trace.jsonl"
            # C2: forward the G2 shaping terms (extreme_risk_contrib_penalty /
            # realrate_cap_contrib_penalty) into params_override so the C#
            # strategy's effCap = base_cap / penalty actually diverges from G1.
            # Without this the shaping bundle computes a non-1.0 weight but the
            # run silently uses G0 defaults (G2 behavior == G1).
            p = req.parameters
            r = _lean_run(cdir, f"{wid}-G2-{req.candidate_id}", "G2",
                          req.candidate_id, wid, _iso(w.train[0]),
                          _iso(w.train[1]), ctr, params_override=p)
            if not r.is_success():
                return TrialResult(status="FAILED_STRATEGY", metrics=None, error=r.error)
            return TrialResult(status="SUCCEEDED",
                               metrics=_metrics(cdir, f"{wid}-G2-{req.candidate_id}"),
                               error=None)

        g2 = FeedbackConstructionAdapter(
            g2_runner, budget=2, seed=17, journal_path=wdir / "g2-events.jsonl",
        ).run(bundle, f"{wid}/train", f"{wid}/review")

        report["windows"].append({
            "window_id": wid,
            "train": [_iso(w.train[0]), _iso(w.train[1])],
            "g0_status": g0.status,
            "g0_metrics": g0m,
            "review_validity": review.validity_status,
            "review_invalid_reason": review.invalid_reason,
            "g1_attempted": g1.attempted_trial_count,
            "g1_selected": (g1.selected_candidate.candidate_id
                            if g1.selected_candidate else None),
            "g2_attempted": g2.attempted_trial_count,
            "g2_validity": g2.validity_status,
            "g2_shaping": g2.shaping_overrides,
            "g2_aliased_to_g1": g2.aliased_to_g1,
        })
        print(f"[{wid}] G0={g0.status} sharpe={g0m['sharpe']:.3f} "
              f"review={review.validity_status} "
              f"G1 tried={g1.attempted_trial_count} sel={g1.selected_candidate is not None} "
              f"G2 tried={g2.attempted_trial_count} validity={g2.validity_status} "
              f"alias_g1={g2.aliased_to_g1}", flush=True)

    valid = [w for w in report["windows"]
             if w["review_validity"] == "VALID" and w["g2_validity"] == "VALID"]
    report["valid_window_count"] = len(valid)
    report["stop_p2_verdict"] = "PASS" if len(valid) >= 3 else "FAIL"
    (out_root / "p2_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n")
    print(f"\nSTOP P2: {len(valid)}/4 valid formal G0/G1/G2 windows -> "
          f"{report['stop_p2_verdict']}")
    return 0 if report["stop_p2_verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
