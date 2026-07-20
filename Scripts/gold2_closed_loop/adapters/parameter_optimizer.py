"""Proof-local G1 parameter optimizer adapter with complete attempt
accounting (Task 10, Phase 2 STOP gate).

This is the G1 parameter-optimization stage. It is FULLY INDEPENDENT of
the production ``evolution_scheduler`` / ``layer_state`` / ``inspiration``
machinery — proof-local, no reuse. Every trial (success, strategy
failure, infrastructure failure, timeout, no-trade, prune, duplicate,
dominated, rejected, invalid) is recorded as a hash-chained candidate
event in the ``EventJournal`` and consumes the search budget.

Anti-p-hacking discipline
------------------------
You cannot silently drop failed attempts. The adapter:

1. Generates candidate parameter sets from the declared ``parameter_space``
   using a seeded ``random.Random(seed)`` (deterministic given the seed).
2. For each candidate (up to ``budget`` total trials):
   a. Builds a typed ``OptimizationRequest`` with a fresh ``candidate_id``.
   b. REGISTERs the candidate (``execution_status=PENDING``) in the journal
      BEFORE invoking the runner.
   c. Invokes the runner. Catches and classifies exceptions:
      - ``TimeoutError`` -> TIMED_OUT
      - ``InfrastructureError`` (or message prefix ``"infrastructure:"``)
        -> FAILED_INFRASTRUCTURE
      - any other exception -> FAILED_STRATEGY
      A returned ``TrialResult`` with a set ``status`` is honored.
   d. If the runner returned metrics: classify result — ``trades == 0``
      -> NO_TRADES; else apply gates (min trades / max MDD / min DSR /
      parameter bounds / train-subwindow stability) -> SUCCEEDED / PRUNED
      / REJECTED; then dedup (DUPLICATE) and dominance (DOMINATED).
   e. Records the terminal candidate event and the attempt.
3. Selects the best passing candidate by the preregistered ranking metric
   (default sharpe desc) with tie-break (net_profit desc, mdd asc).

G1 never aliases to G0: if nothing passes the gates,
``selected_candidate=None`` and ``aliased_to_g0=False``. The G1-alias-G0
failure handling from design §14 is a SEPARATE downstream concern.
"""

from __future__ import annotations

import itertools
import random
from pathlib import Path
from typing import Any, Callable

from Scripts.gold2_closed_loop.adapters.base import (
    Adapter,
    Attempt,
    OptimizationRequest,
    OptimizationResult,
    Runner,
    TrialResult,
)
from Scripts.gold2_closed_loop.candidate_registry import CandidateRegistry
from Scripts.gold2_closed_loop.selection import (
    classify_outcome,
    select,
)


class InfrastructureError(Exception):
    """Marker exception classifying a runner failure as infrastructure.

    A runner failure caused by infrastructure (dotnet missing, file
    system error, result packet missing) is FAILED_INFRASTRUCTURE, not
    FAILED_STRATEGY. The adapter also recognizes a message prefix
    ``"infrastructure:"`` as an infrastructure failure so a plain
    ``RuntimeError("infrastructure: ...")`` is classified correctly
    without requiring the caller to import this class.
    """


# Sensible unit-test defaults. Production callers should pass a real
# ``preregistration`` dict with ``success_thresholds`` / ``g3`` so the
# gates are preregistered, not these defaults.
_DEFAULT_MIN_TRADES = 1
_DEFAULT_MAX_MDD = 0.5
_DEFAULT_MIN_DSR = 0.0

# Cap the candidate grid at this size so a pathological parameter space
# (e.g. a range with step=0.001 over [0, 1] = 1001 values, times 4 other
# params) cannot explode the optimizer into OOM. The budget is the
# hard limit on trials actually RUN; this cap only limits the in-memory
# grid generation before budget truncation.
_MAX_GRID = 10_000


class ParameterOptimizerAdapter(Adapter):
    """Proof-local G1 parameter optimizer.

    Constructor signature keeps the plan's 4-positional-arg form
    (``runner, budget, seed, journal_path``) working; keyword-only args
    follow.

    Parameters
    ----------
    runner:
        Callable ``runner(request) -> TrialResult``. May raise
        (``TimeoutError`` / ``InfrastructureError`` / generic
        ``Exception``); the adapter catches and classifies.
    budget:
        Maximum number of trials to run. Each trial (success OR failure)
        consumes one unit of budget.
    seed:
        Seed for the ``random.Random`` used to shuffle the candidate grid
        (deterministic given the seed).
    journal_path:
        Path to the hash-chained ``EventJournal`` JSONL file.
    preregistration:
        Optional validated preregistration dict. Gates (min trades / max
        MDD / min DSR / parameter bounds / train-subwindow stability) are
        read from ``preregistration["success_thresholds"]`` if present.
    gates:
        Explicit gates dict (for unit tests). Keys: ``min_trades``,
        ``max_mdd``, ``min_dsr``, ``subwindow_spread_max``. Overrides
        preregistration-derived gates when both are present.
    """

    def __init__(
        self,
        runner: Runner | Callable[[OptimizationRequest], TrialResult],
        budget: int,
        seed: int,
        journal_path: Path | str,
        *,
        preregistration: dict[str, Any] | None = None,
        gates: dict[str, Any] | None = None,
        experiment_id: str = "gold2-proof",
        window_id: str = "W1",
        stage_id: str = "G1",
        run_dir: Path | str | None = None,
        trace_path: Path | str | None = None,
        ranking_metric: str | None = None,
    ) -> None:
        if budget < 1:
            raise ValueError(
                f"ParameterOptimizerAdapter: budget must be >= 1, got {budget}"
            )
        self._runner = runner
        self._budget = int(budget)
        self._seed = int(seed)
        self._journal_path = Path(journal_path)
        self._preregistration = preregistration
        self._gates = self._resolve_gates(gates, preregistration)
        self._experiment_id = str(experiment_id)
        self._window_id = str(window_id)
        self._stage_id = str(stage_id)
        self._run_dir = Path(run_dir) if run_dir is not None else Path(".")
        self._trace_path = Path(trace_path) if trace_path is not None else None
        # Ranking metric + tie-break: sourced from the preregistration's
        # `selection` block (whole-tree review: these were hard-coded kwargs
        # / constants, never preregistered, so two runs could select G1 by
        # different metrics with no audit trail). The explicit kwarg still
        # overrides (unit-test back-door), but the preregistered value is
        # the production source.
        sel = (preregistration or {}).get("selection") or {}
        self._ranking_metric = str(
            ranking_metric if ranking_metric is not None
            else sel.get("ranking_metric", "sharpe")
        )
        self._tie_break = sel.get("tie_break") or [
            {"metric": "net_profit", "direction": "desc"},
            {"metric": "mdd", "direction": "asc"},
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self, parameter_space: dict[str, Any], partition: str
    ) -> OptimizationResult:
        """Generate the candidate grid, run trials up to budget, select."""
        if not isinstance(parameter_space, dict):
            raise TypeError(
                f"parameter_space must be a dict, got "
                f"{type(parameter_space).__name__}"
            )
        if not partition:
            raise ValueError("partition must be non-empty")

        # Generate the (shuffled) candidate grid.
        grid = self._generate_grid(parameter_space)
        rng = random.Random(self._seed)
        # Deterministic shuffle of the grid indices. We shuffle a list
        # of indices (not the grid itself) so the grid generation order
        # is stable and the shuffle is purely a seed-controlled
        # reordering of WHICH candidates are tried first within budget.
        order = list(range(len(grid)))
        rng.shuffle(order)

        # The registry wraps the journal. Each register / record_outcome
        # call appends one hash-chained record.
        registry = CandidateRegistry(self._journal_path)

        attempts: list[Attempt] = []
        # Accepted (passing gates) attempts, used for dominance / dedup
        # classification of later candidates.
        passing: list[Attempt] = []
        trial_count = 0

        for idx in order:
            if trial_count >= self._budget:
                break
            parameters = grid[idx]
            candidate_id = f"{self._stage_id}-{partition}-{trial_count}"
            request = OptimizationRequest(
                experiment_id=self._experiment_id,
                window_id=self._window_id,
                stage_id=self._stage_id,
                candidate_id=candidate_id,
                partition=partition,
                parameter_space=parameter_space,
                parameters=parameters,
                seed=self._seed,
                budget=self._budget - trial_count,
                run_dir=self._run_dir,
                trace_path=self._trace_path,
                preregistration=self._preregistration,
            )
            # REGISTER before execution: PENDING event with the candidate's
            # parameters + partition. This is the anti-p-hacking core:
            # every trial is accounted BEFORE we know its outcome.
            registry.register(
                candidate_id=candidate_id,
                partition=partition,
                parameters=parameters,
                execution_status="PENDING",
            )
            # Invoke the runner. Classify exceptions.
            status, metrics, error, event_type = self._invoke(request)
            # If the runner returned metrics, apply gates / dedup /
            # dominance to refine the terminal event_type.
            if status == "SUCCEEDED" and metrics is not None:
                event_type = classify_outcome(
                    Attempt(
                        candidate_id=candidate_id,
                        parameters=parameters,
                        status="SUCCEEDED",
                        event_type="SUCCEEDED",
                        metrics=metrics,
                        error=error,
                    ),
                    passing_attempts=passing,
                    gates=self._gates,
                )
                # Map REJECTED/PRUNED/DUPLICATE/DOMINATED to an
                # ExecutionStatus. These are terminal candidate-event
                # types but the ExecutionStatus enum does not have them;
                # use SUCCEEDED for the execution_status (the runner
                # DID succeed) — the event_type carries the
                # classification.
                if event_type == "SUCCEEDED":
                    exec_status = "SUCCEEDED"
                elif event_type == "DUPLICATE":
                    exec_status = "SUCCEEDED"
                elif event_type == "DOMINATED":
                    exec_status = "SUCCEEDED"
                elif event_type == "PRUNED":
                    exec_status = "SUCCEEDED"
                elif event_type == "REJECTED":
                    exec_status = "SUCCEEDED"
                elif event_type == "INVALID":
                    # The runner returned a result, but the metrics are
                    # non-finite (NaN/±Infinity). The execution itself
                    # succeeded (the runner ran); the result is unusable.
                    # Follows the SAME convention as PRUNED/DOMINATED:
                    # execution_status reflects whether the runner ran
                    # (SUCCEEDED); event_type carries the classification
                    # (INVALID). See plan Step 3 "invalid attempt
                    # consumes budget".
                    exec_status = "SUCCEEDED"
                else:
                    exec_status = "SUCCEEDED"
            else:
                # Runner failure: event_type already set by _invoke;
                # execution_status matches the failure event_type.
                exec_status = event_type
            # INVALID attempts have non-finite metrics. The candidate-event
            # schema (§13 finite-value check) rejects NaN/±Infinity in
            # ``metrics``, so we cannot persist the raw non-finite dict.
            # Persist metrics=None for INVALID: the result is unusable, so
            # storing the offending values would (a) fail schema validation
            # and (b) mislead downstream reviewers into ranking a NaN
            # sharpe. The classification (event_type=INVALID) is the
            # signal; the raw non-finite metrics are discarded.
            if event_type == "INVALID":
                recorded_metrics: dict[str, Any] | None = None
            else:
                recorded_metrics = metrics
            attempt = Attempt(
                candidate_id=candidate_id,
                parameters=parameters,
                status=exec_status,
                event_type=event_type,
                metrics=metrics,
                error=error,
            )
            # Record the terminal event in the journal.
            registry.record_outcome(
                candidate_id=candidate_id,
                event_type=event_type,
                execution_status=exec_status,
                partition=partition,
                parameters=parameters,
                metrics=recorded_metrics,
            )
            attempts.append(attempt)
            if event_type == "SUCCEEDED":
                passing.append(attempt)
            trial_count += 1

        # Selection: pure function over attempts. The selected candidate
        # is the best passing Attempt by the preregistered ranking metric
        # with tie-break, or None if nothing passes.
        selected = select(
            attempts, ranking_metric=self._ranking_metric, gates=self._gates
        )
        return OptimizationResult(
            attempts=attempts,
            attempted_trial_count=trial_count,
            selected_candidate=selected,
            aliased_to_g0=False,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _invoke(
        self, request: OptimizationRequest
    ) -> tuple[str, dict[str, Any] | None, str | None, str]:
        """Invoke the runner, catch + classify exceptions.

        Returns ``(execution_status, metrics, error, event_type)``.
        """
        try:
            result = self._runner(request)
        except TimeoutError as exc:
            return ("TIMED_OUT", None, str(exc), "TIMED_OUT")
        except InfrastructureError as exc:
            return (
                "FAILED_INFRASTRUCTURE",
                None,
                str(exc),
                "FAILED_INFRASTRUCTURE",
            )
        except Exception as exc:
            # Classify: a message prefix "infrastructure:" marks the
            # failure as infrastructure, otherwise it is a strategy
            # failure.
            msg = str(exc)
            if msg.lower().startswith("infrastructure:"):
                return (
                    "FAILED_INFRASTRUCTURE",
                    None,
                    msg,
                    "FAILED_INFRASTRUCTURE",
                )
            return ("FAILED_STRATEGY", None, msg, "FAILED_STRATEGY")
        # Contract guard (defect 4, Task 10 code-quality review): a runner
        # that returns None (e.g. a wrapper missing its return statement)
        # is a contract violation, not a crash. Treat it as an
        # infrastructure failure so the candidate is closed with a
        # terminal FAILED_INFRASTRUCTURE event rather than crashing at
        # ``result.status`` and leaving a dangling PENDING record.
        if result is None:
            return (
                "FAILED_INFRASTRUCTURE",
                None,
                "runner returned None (contract violation: expected TrialResult)",
                "FAILED_INFRASTRUCTURE",
            )
        # Runner returned a TrialResult. Honor its status, then refine
        # for NO_TRADES (trades == 0) below.
        status = result.status
        metrics = result.metrics
        error = result.error
        # If the runner returned SUCCEEDED with metrics but trades == 0,
        # this is NO_TRADES (design §10).
        if status == "SUCCEEDED" and isinstance(metrics, dict):
            trades = metrics.get("trades")
            if trades is not None:
                try:
                    if float(trades) == 0.0:
                        return ("NO_TRADES", metrics, error, "NO_TRADES")
                except (TypeError, ValueError):
                    pass
        # Map ExecutionStatus to the matching CandidateEventType for
        # terminal-failure statuses. SUCCEEDED is refined later by the
        # gate classification.
        if status == "SUCCEEDED":
            return ("SUCCEEDED", metrics, error, "SUCCEEDED")
        # status is already a failure ExecutionStatus value; the
        # CandidateEventType has the same name for FAILED_STRATEGY /
        # FAILED_INFRASTRUCTURE / FAILED_EVIDENCE_CAPTURE / TIMED_OUT /
        # NO_TRADES.
        return (status, metrics, error, status)

    def _resolve_gates(
        self,
        gates: dict[str, Any] | None,
        preregistration: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Resolve gates from explicit arg or preregistration.

        Explicit ``gates`` arg overrides preregistration-derived gates.
        If neither is provided, use sensible unit-test defaults.
        """
        if gates is not None:
            resolved = dict(gates)
        elif preregistration is not None:
            # Read the ABSOLUTE within-generation MDD cap from
            # ``candidate_gates.max_mdd``. Do NOT source it from
            # ``success_thresholds.max_drawdown_degradation`` — that is a
            # CROSS-GENERATION DELTA (design §13: candidate_mdd -
            # parent_mdd <= limit), not an absolute cap. Conflating the
            # two would prune a viable 15%-drawdown G1 candidate under a
            # 0.10 degradation delta, falsely forcing a G1->G0 alias
            # (defect 2, Task 10 code-quality review). G1 has no parent
            # MDD to diff against; the degradation check belongs to
            # G2/G3, not the G1 absolute gate.
            candidate_gates = preregistration.get("candidate_gates", {}) or {}
            resolved = {
                "max_mdd": candidate_gates.get("max_mdd", _DEFAULT_MAX_MDD),
                "min_trades": candidate_gates.get(
                    "min_trades", _DEFAULT_MIN_TRADES
                ),
                "min_dsr": candidate_gates.get("min_dsr", _DEFAULT_MIN_DSR),
            }
        else:
            resolved = {
                "min_trades": _DEFAULT_MIN_TRADES,
                "max_mdd": _DEFAULT_MAX_MDD,
                "min_dsr": _DEFAULT_MIN_DSR,
            }
        # Ensure the canonical keys exist even if the caller did not
        # supply them.
        resolved.setdefault("min_trades", _DEFAULT_MIN_TRADES)
        resolved.setdefault("max_mdd", _DEFAULT_MAX_MDD)
        resolved.setdefault("min_dsr", _DEFAULT_MIN_DSR)
        return resolved

    def _generate_grid(
        self, parameter_space: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Cartesian product of each parameter's value list.

        Parameter space spec (per param name):
          ``{"type": "bool"}``                              -> [False, True]
          ``{"type": "range", "min": x, "max": y, "step": s}``
              -> arithmetic sequence min..max step s (inclusive)
          ``{"type": "range", "min": x, "max": y, "count": n}``
              -> min + i*(max-min)/(n-1) for i in 0..n-1 (linspace)
          ``{"type": "choice", "values": [...]}``           -> the values
        """
        if not parameter_space:
            return [{}]
        names = list(parameter_space.keys())
        value_lists: list[list[Any]] = []
        for name in names:
            spec = parameter_space[name]
            if not isinstance(spec, dict):
                raise TypeError(
                    f"parameter_space[{name!r}] must be a dict spec, got "
                    f"{type(spec).__name__}"
                )
            ptype = spec.get("type")
            if ptype == "bool":
                value_lists.append([False, True])
            elif ptype == "range":
                value_lists.append(_range_values(spec))
            elif ptype == "choice":
                vals = spec.get("values")
                if not isinstance(vals, list):
                    raise ValueError(
                        f"parameter_space[{name!r}] choice requires a "
                        f"'values' list"
                    )
                value_lists.append(list(vals))
            else:
                raise ValueError(
                    f"parameter_space[{name!r}] unknown type {ptype!r}; "
                    "expected 'bool' / 'range' / 'choice'"
                )
        # Cartesian product.
        grid: list[dict[str, Any]] = []
        for combo in itertools.product(*value_lists):
            grid.append(dict(zip(names, combo)))
            if len(grid) >= _MAX_GRID:
                raise ValueError(
                    f"parameter grid exceeded {_MAX_GRID} candidates; "
                    "reduce the parameter space or increase step"
                )
        return grid


def _range_values(spec: dict[str, Any]) -> list[Any]:
    """Expand a range spec into its list of values.

    ``{"min": x, "max": y, "step": s}``  -> arithmetic sequence
    (inclusive of max when it lands on a step boundary).
    ``{"min": x, "max": y, "count": n}`` -> linspace of n points
    (inclusive of both endpoints).
    """
    if "min" not in spec or "max" not in spec:
        raise ValueError(
            "range spec requires 'min' and 'max'"
        )
    lo = spec["min"]
    hi = spec["max"]
    try:
        lo_n = float(lo)
        hi_n = float(hi)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"range min/max must be numeric, got {lo!r} / {hi!r}"
        ) from error
    if "count" in spec:
        count = int(spec["count"])
        if count < 1:
            raise ValueError(f"range count must be >= 1, got {count}")
        if count == 1:
            return [lo_n]
        step = (hi_n - lo_n) / (count - 1)
        out = [lo_n + i * step for i in range(count)]
        # Force the last point to be exactly hi to avoid float drift.
        out[-1] = hi_n
        # Preserve int-ness if both endpoints are ints and the step is
        # integral (so bool/choice tests comparing values get clean ints).
        return _maybe_int(out, lo, hi)
    if "step" in spec:
        step = spec["step"]
        try:
            step_n = float(step)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"range step must be numeric, got {step!r}"
            ) from error
        if step_n <= 0:
            raise ValueError(f"range step must be > 0, got {step_n}")
        # Arithmetic sequence inclusive of hi when it lands on a step.
        out: list[float] = []
        # Use a small epsilon to tolerate float drift at the boundary.
        eps = step_n * 1e-9
        v = lo_n
        while v <= hi_n + eps:
            out.append(v)
            v += step_n
        return _maybe_int(out, lo, hi)
    raise ValueError(
        "range spec requires either 'step' or 'count'"
    )


def _maybe_int(values: list[float], lo: Any, hi: Any) -> list[Any]:
    """Coerce float values back to int if both endpoints are ints.

    Keeps bool params clean for tests that compare ``isinstance(x, int)``.
    """
    if isinstance(lo, int) and isinstance(hi, int):
        return [int(round(v)) for v in values]
    return values
