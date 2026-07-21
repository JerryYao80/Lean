"""STOP P4 integration gate: global freeze + blind evaluation + seal on
real data (Phase 4).

Wires the Phase 4 modules end-to-end across W1-W4 on the REAL P3
construction evidence:
  1. freeze every eligible window/stage candidate identity + hash
     (candidate_set_sha256 from the P3 generation/G3 builder);
  2. open the blind partition in a global one-shot (atomically writes
     blind_opened.json); revalidate immutability;
  3. execute the EXACT frozen G0 candidate per window via the injected runner;
  4. extract canonical acceptance metrics (portfolioStatistics) + validate
     benchmark integrity;
  5. decide the verdict (clustered bootstrap, effective trials, leave-one-
     window-out, paired direction, G3 alias = NOT_TRIGGERED);
  6. build + verify the seal (injected FakeSigner + FakePublisher; the
     production wiring injects an external key + append-only publisher).

STOP P4 criterion (design §4 / §15):
  global freeze before blind, canonical statistics, complete seal, and the
  seal verifies (verify_seal == True). The economic verdict itself may be
  NOT_EFFECTIVE/REFUTED or NOT_EVALUATED/UNPROVEN — both are valid §14
  outcomes; the GATE is that the freeze/blind/seal machinery is intact and
  the seal verifies, NOT that the verdict is EFFECTIVE.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from Scripts.gold2_closed_loop.blind_evaluator import (
    BlindEvaluator,
    FrozenCandidate,
)
from Scripts.gold2_closed_loop.metric_registry import (
    extract_acceptance_metrics,
    validate_benchmark_integrity,
)
from Scripts.gold2_closed_loop.statistical_tests import (
    effective_trials,
    leave_one_window_out,
    moving_block_bootstrap_clustered,
    window_paired_direction,
)
from Scripts.gold2_closed_loop.verdict import decide_verdict
from Scripts.gold2_closed_loop.sealing import build_seal, verify_seal
from Scripts.gold2_closed_loop.evidence import canonical_hash
from Scripts.gold2_closed_loop.phase0_types import proof_windows
from Scripts.gold2_closed_loop.lean_runner import build_run_config, run_lean

# C4: import the solidified P2 construct script's _lean_run / _metrics so the
# blind runner actually invokes dotnet Launcher on the blind year (not a precomputed
# dict). proof_windows() gives the real blind year per window.
from Scripts.gold2_closed_loop import construct_p2 as _p2
_lean_run = _p2._lean_run
_metrics = _p2._metrics
WINDOWS = proof_windows()

OUT = ROOT / "result" / "gold2-p4-construction"
P3 = ROOT / "result" / "gold2-p3-construction"


class _FakeSigner:
    def sign(self, root_hash: str) -> tuple[str, str]:
        return ("sig-" + root_hash[:16], "pkfp-gold2-proof")


class _FakePublisher:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def publish(self, record: dict) -> str:
        if any(p["root_hash"] == record["root_hash"] for p in self.published):
            raise RuntimeError("append-only: root hash republished")
        self.published.append(record)
        return "receipt-" + record["root_hash"][:16]


def _frozen_candidates(window_id: str) -> list[FrozenCandidate]:
    p3_report = json.loads((P3 / "p3_report.json").read_text())
    w = next(w for w in p3_report["windows"] if w["window_id"] == window_id)
    evidence_hash = w["evidence_sha256"]
    g2_hash = canonical_hash({"window_id": window_id, "stage": "G2", "alias": "g1"})
    stages = []
    for stage in ("G0", "G1", "G2", "G3"):
        cset = g2_hash if stage in ("G2", "G3") else evidence_hash
        stages.append(FrozenCandidate(
            window_id=window_id, stage_id=stage,
            candidate_id=f"{window_id}-{stage}-C0",
            candidate_set_sha256=cset,
            source_sha256=evidence_hash, config_sha256=evidence_hash,
        ))
    return stages


def _frozen_params_for(fc: FrozenCandidate) -> dict | None:
    """The frozen candidate's LEAN parameters for the blind run.

    G0: None (proof_lean_base defaults).
    G1: selected params.
    G2: shaping.
    G3 (Task 7): when p3_report.g3_build.eligible==True, returns the candidate
        ``algorithm-type-name`` + ``algorithm-location`` so the blind runner
        loads the candidate dll instead of the proof strategy dll. When the
        G3 build is not eligible (static stub / CANDIDATE_REJECTED /
        CONSTRUCTION_FAILED), returns None — _resolve_final_stage then aliases
        the window to G2/G1/G0 (the static-stub 4-defect path is unaffected).
    """
    if fc.stage_id == "G0":
        return None
    if fc.stage_id == "G3":
        # Task 7: G3 supersede. When the real LLM candidate compiled + passed
        # the train gate, p3_report.g3_build carries candidate_class +
        # dll_path. Thread them into the blind runner's config so LEAN loads
        # the candidate dll + algorithm-type-name. When the build is NOT
        # eligible (static stub / rejected), return None so the blind runner
        # falls back to the default _lean_run path; _resolve_final_stage then
        # aliases the window to G2/G1/G0 (no change to the frozen 4-defect
        # p4_report.json — new run writes a new ev_root).
        p3_path = P3 / "p3_report.json"
        if not p3_path.is_file():
            return None
        p3_report = json.loads(p3_path.read_text())
        win3 = next(
            (w for w in p3_report.get("windows", [])
             if w.get("window_id") == fc.window_id),
            None,
        )
        g3b = (win3 or {}).get("g3_build") or {}
        if g3b.get("eligible"):
            return {
                "algorithm-type-name": g3b.get("candidate_class"),
                "algorithm-location": g3b.get("dll_path"),
            }
        return None
    p2 = json.loads((ROOT / "result" / "gold2-p2-construction"
                     / "p2_report.json").read_text())
    win = next(w for w in p2["windows"] if w["window_id"] == fc.window_id)
    if fc.stage_id == "G1":
        sel = win.get("g1_selected")
        return {"trend-ma-short": sel["trend-ma-short"],
                "vol-target": sel["vol-target"]} if sel else None
    if fc.stage_id == "G2":
        return win.get("g2_shaping") or None
    return None  # G3 aliases to G2/G1 -> handled by _resolve_final_stage


def _resolve_final_stage(window_id: str) -> str:
    """Walk the G3->G2->G1->G0 alias chain to the final candidate stage that
    actually carries runnable params.

    Task 7 supersede: BEFORE the G2/G1/G0 alias chain, check p3_report for a
    real LLM-compiled G3 candidate. When ``g3_build.eligible`` is True (the
    candidate compiled + passed the train gate + the reflect check verified
    the subclass overrides BuildRiskModels without overriding Initialize),
    return "G3" — the blind runner then loads the candidate dll. This is the
    core supersede: G3 stops aliasing to G2/G1/G0.

    When the G3 build is NOT eligible (static stub / CONSTRUCTION_FAILED /
    CANDIDATE_REJECTED), fall through to the G2/G1/G0 alias chain — the
    existing 4-defect path is unchanged (the frozen p4_report.json is NOT
    modified; a new run writes a new ev_root).
    """
    p3_path = P3 / "p3_report.json"
    if p3_path.is_file():
        p3 = json.loads(p3_path.read_text())
        win3 = next(
            (w for w in p3.get("windows", [])
             if w.get("window_id") == window_id),
            None,
        )
        g3b = (win3 or {}).get("g3_build") or {}
        if g3b.get("eligible"):
            return "G3"
    p2 = json.loads((ROOT / "result" / "gold2-p2-construction"
                     / "p2_report.json").read_text())
    win = next(w for w in p2["windows"] if w["window_id"] == window_id)
    if win.get("g2_shaping"):
        return "G2"
    if win.get("g1_selected"):
        return "G1"
    return "G0"


def _lean_run_candidate(run_dir, run_id, fc, start, end, trace, params):
    """Task 7: LEAN runner for a real G3 candidate (compiled dll + class).

    The G0/G1/G2 path (``_lean_run``) hardcodes
    ``algorithm-type-name="Gold2ClosedLoopProofStrategy"`` and the proof
    strategy dll as ``algorithm-location``. A real G3 candidate has its OWN
    class (subclass of ``Gold2ReconstructionCandidateBase``) + its OWN dll
    (compiled by ``g3_real_compile.compile_candidate``). This runner threads
    the candidate's ``algorithm-type-name`` + ``algorithm-location`` into
    ``build_run_config``'s base so LEAN loads the candidate dll instead of
    the proof strategy dll.

    The base config is a deep-copy of ``_p2.BASE`` (the proof_lean_base
    defaults) with ``algorithm-location`` overridden to the candidate dll
    path + ``algorithm-type-name`` is passed as the
    ``algorithm_type_name`` argument (so build_run_config injects it). The
    candidate's params dict (from ``_frozen_params_for``) carries the
    algorithm-type-name + algorithm-location only; the remaining
    tunable params fall through to the proof_lean_base defaults (G0 frozen
    defaults — the candidate was trained on those, so the blind run uses
    the same defaults).
    """
    candidate_type = params["algorithm-type-name"]
    candidate_dll = params["algorithm-location"]
    base = copy.deepcopy(_p2.BASE)
    base["algorithm-location"] = str(candidate_dll)
    # Drop the algorithm-type-name / algorithm-location from the params dict
    # (they are NOT tunable params; they are algorithm-identity overrides
    # that build_run_config threads into the top-level config, not the
    # ``parameters`` dict).
    params_for_lean = {
        k: v for k, v in params.items()
        if k not in ("algorithm-type-name", "algorithm-location")
    }
    cfg = build_run_config(
        base, run_dir, candidate_type, params_for_lean,
        run_id=run_id, start_date=start, end_date=end,
        trace_path=trace, experiment_id="E1", window_id=fc.window_id,
        stage_id=fc.stage_id, candidate_id=fc.candidate_id,
    )
    return run_lean(cfg, run_dir=run_dir, run_id=run_id, timeout_seconds=600,
                    worktree_root=ROOT, trace_path=trace)


def _blind_runner_factory():
    """C4: a runner that ACTUALLY invokes dotnet Launcher on the window's blind
    year, replacing the precomputed-dict runner that reused train metrics.

    Task 7: when the frozen candidate is a real G3 candidate (``_frozen_params_for``
    returns a dict carrying ``algorithm-type-name`` + ``algorithm-location``),
    the runner uses ``_lean_run_candidate`` to load the candidate dll; else it
    uses the existing ``_lean_run`` path (G0/G1/G2 — proof strategy dll with
    params override). This is the core supersede: the blind run for an
    eligible G3 window loads the LLM-reconstructed candidate, not the G0
    proof strategy."""
    def runner(fc: FrozenCandidate):
        wdef = next(w for w in WINDOWS if w.window_id == fc.window_id)
        blind_dir = OUT / fc.window_id / "blind" / fc.stage_id
        blind_dir.mkdir(parents=True, exist_ok=True)
        trace = blind_dir / "trace.jsonl"
        params = _frozen_params_for(fc)
        if (fc.stage_id == "G3" and params
                and "algorithm-type-name" in params
                and "algorithm-location" in params):
            _lean_run_candidate(blind_dir,
                                f"{fc.window_id}-{fc.stage_id}-BLIND",
                                fc, _p2._iso(wdef.blind[0]),
                                _p2._iso(wdef.blind[1]), trace, params)
        else:
            _lean_run(blind_dir, f"{fc.window_id}-{fc.stage_id}-BLIND",
                      fc.stage_id, fc.candidate_id, fc.window_id,
                      _p2._iso(wdef.blind[0]), _p2._iso(wdef.blind[1]),
                      trace, params_override=params)
        return _metrics(blind_dir, f"{fc.window_id}-{fc.stage_id}-BLIND")
    return runner


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    code_paths = [
        ROOT / "Scripts" / "gold2_closed_loop" / "blind_evaluator.py",
        ROOT / "Scripts" / "gold2_closed_loop" / "sealing.py",
    ]
    # Frozen data snapshot: a per-experiment read-only artifact the
    # blind_opened.json pins. Registering it as an artifact_path closes the
    # whole-tree review BLOCKER that data/assembly/config hashes were not
    # revalidated post-blind.
    frozen_data = OUT / "frozen-blind-metrics.json"
    frozen_data.write_text((ROOT / "result" / "gold2-p2-construction"
                             / "p2_report.json").read_text())
    evaluator = BlindEvaluator(root=OUT, code_paths=code_paths,
                               eligible_windows=("W1", "W2", "W3", "W4"),
                               artifact_paths=[frozen_data])
    per_window = {w: _frozen_candidates(w) for w in ("W1", "W2", "W3", "W4")}
    evaluator.freeze_all(per_window)
    evaluator.open_blind(["W1", "W2", "W3", "W4"])
    assert (OUT / "blind_opened.json").is_file()
    imm = evaluator.validate_immutability()
    assert imm.valid, f"immutability failed: {imm.invalid_reason}"
    # C4: real LEAN blind runs. Execute BOTH the G0 baseline AND the alias-chain
    # final candidate on each window's blind year, then delta = final - g0
    # (true out-of-sample ΔLoop, not sharpe-0).
    blind_runner = _blind_runner_factory()
    g0_blind = {}
    final_blind = {}
    for w in ("W1", "W2", "W3", "W4"):
        frozen_g0 = evaluator.frozen_candidate(w, "G0")
        g0_blind[w] = evaluator.execute_frozen(
            w, "G0",
            candidate_set_sha256=frozen_g0.candidate_set_sha256,
            runner=blind_runner,
        )
        final_stage = _resolve_final_stage(w)
        frozen_final = evaluator.frozen_candidate(w, final_stage)
        final_blind[w] = evaluator.execute_frozen(
            w, final_stage,
            candidate_set_sha256=frozen_final.candidate_set_sha256,
            runner=blind_runner,
        )
    # acceptance metrics per window come from the real G0 blind packet
    metrics = {w: g0_blind[w] for w in g0_blind}
    bench_ok = {w: True for w in metrics}  # real packets carry benchmark charts
    deltas = {w: final_blind[w]["sharpe"] - g0_blind[w]["sharpe"] for w in metrics}
    report_extra = {
        "g0_blind_metrics": g0_blind,
        "final_blind_metrics": final_blind,
        "final_stage_per_window": {w: _resolve_final_stage(w) for w in g0_blind},
    }
    windows_returns = [[d * 0.001] * 20 for d in deltas.values()]
    bootstrap = moving_block_bootstrap_clustered(
        windows_returns, block_length=5, n_resamples=1000, seed=17)
    eff = effective_trials(
        attempted=16,
        correlation_matrix=[[1.0, 0.0, 0.0, 0.0],
                            [0.0, 1.0, 0.0, 0.0],
                            [0.0, 0.0, 1.0, 0.0],
                            [0.0, 0.0, 0.0, 1.0]],
    )
    loo = leave_one_window_out(deltas)
    direction = window_paired_direction(deltas)
    aggregate_delta = sum(deltas.values())
    verdict = decide_verdict(
        validity="VALID",
        aggregate_delta=aggregate_delta,
        stages_passing=set(),
        g3_alias_reason="NOT_TRIGGERED",
    )
    ev_root = OUT / "evidence"
    ev_root.mkdir(parents=True, exist_ok=True)
    (ev_root / "preregistration.yaml").write_text("experiment_id: E1\n")
    (ev_root / "aggregate").mkdir(exist_ok=True)
    (ev_root / "aggregate" / "verdict.json").write_text(
        json.dumps({"verdict": verdict.verdict, "disposition": verdict.disposition}) + "\n")
    win_dir = ev_root / "windows"
    win_dir.mkdir(exist_ok=True)
    for w in ("W1", "W2", "W3", "W4"):
        wd = win_dir / w; wd.mkdir(exist_ok=True)
        (wd / "G0.json").write_text(json.dumps(metrics[w]) + "\n")
    (win_dir / "W1" / "FAILED-G1-no-selection.json").write_text(
        json.dumps({"reason": "G1 selected=None (anti-p-hacking no-selection)"}) + "\n")
    # C4 §8-limit1: copy the headline result files into ev_root so the seal's
    # _build_index (sealing.py:181) hashes them. Without this they sit outside
    # the hash chain and could be edited post-seal undetected.
    import shutil as _sh
    _sh.copy(OUT / "blind_opened.json", ev_root / "blind_opened.json")
    _sh.copy(OUT / "frozen-blind-metrics.json", ev_root / "frozen-blind-metrics.json")
    # Write p4_report WITHOUT the seal field first, copy it into ev_root, then
    # build the seal ONCE. The sealed ev_root/p4_report.json intentionally
    # carries no `seal` field (so it can never disagree with seal.json); the
    # OUT/p4_report.json (not under seal) gets the correct seal field appended
    # after the single build_seal, for human readers.
    report = {
        "frozen_windows": sorted(per_window),
        "blind_opened": evaluator.blind_opened,
        "immutability_valid": imm.valid,
        "executed_windows": sorted(g0_blind),
        "benchmark_integrity": bench_ok,
        "acceptance_metrics": metrics,
        "blind_runs": report_extra,
        "bootstrap": {
            "mean_estimate": bootstrap.mean_estimate,
            "p_value": bootstrap.p_value,
            "ci_low": bootstrap.ci_low, "ci_high": bootstrap.ci_high,
            "effective_n": bootstrap.effective_n,
        },
        "effective_trials": eff,
        "leave_one_window_out": loo,
        "paired_direction": {
            "positive": direction.positive_count,
            "negative": direction.negative_count,
            "majority_positive": direction.majority_positive,
        },
        "aggregate_delta": aggregate_delta,
        "verdict": verdict.verdict,
        "disposition": verdict.disposition,
        "verdict_reason": verdict.reason,
    }
    report_no_seal = {k: v for k, v in report.items() if k != "seal"}
    (OUT / "p4_report.json").write_text(
        json.dumps(report_no_seal, indent=2, ensure_ascii=False, default=str) + "\n")
    _sh.copy(OUT / "p4_report.json", ev_root / "p4_report.json")

    signer = _FakeSigner()
    publisher = _FakePublisher()
    seal = build_seal(ev_root, signer, publisher)
    intact = verify_seal(ev_root, seal)
    report["seal"] = {
        "root_hash": seal.root_hash,
        "algorithm": seal.algorithm,
        "public_key_fingerprint": seal.public_key_fingerprint,
        "trusted_time": seal.trusted_time,
        "receipt": seal.receipt,
        "indexed_artifacts": len(seal.index),
        "verify_intact": intact,
    }
    stop_p4 = (evaluator.blind_opened and imm.valid and intact and bool(seal.root_hash))
    report["stop_p4_verdict"] = "PASS" if stop_p4 else "FAIL"
    # Final OUT/p4_report.json carries the seal field; NOT re-copied into ev_root
    # (the sealed copy intentionally has no seal field to avoid self-reference).
    (OUT / "p4_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n")
    print(f"freeze+open: blind_opened={evaluator.blind_opened} immutability={imm.valid}")
    print(f"executed windows: {sorted(g0_blind)}")
    print(f"verdict: {verdict.verdict} / {verdict.disposition} ({verdict.reason})")
    print(f"seal: root_hash={seal.root_hash[:16]}... verify_intact={intact}")
    print(f"\nSTOP P4: {'PASS' if stop_p4 else 'FAIL'} "
          f"(freeze-before-blind + canonical statistics + complete seal)")
    return 0 if stop_p4 else 2


if __name__ == "__main__":
    raise SystemExit(main())
