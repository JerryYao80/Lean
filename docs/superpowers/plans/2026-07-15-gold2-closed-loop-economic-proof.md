# Gold2 Closed-Loop Economic Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a staged experiment that determines whether Gold2 construction, parameter optimization, review feedback, and evidence-triggered redesign add fee-inclusive out-of-sample value across frozen 2022–2025 blind windows.

**Architecture:** New code lives only in `Scripts/gold2_closed_loop`, focused proof-only C# files, and corresponding tests. Work stops at five gates: Phase 0 feasibility; one-window G0/G1 plus behavioral equivalence; four-window G0/G1/G2; G3 observability and construction; global freeze, blind evaluation, statistics, verdict, and sealing. Mature Gold2, production manifest/config/daemon/state, and LEAN core stay unchanged.

**Tech Stack:** Python 3, pytest, PyYAML, jsonschema, pandas/NumPy/SciPy, Parquet; C#/.NET 10, NUnit, Newtonsoft.Json; LEAN-native accounting.

**Design:** `docs/superpowers/specs/2026-07-14-gold2-closed-loop-economic-proof-design.md`

---

## Immutable boundaries

Never modify:

- `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs`
- `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`
- `Launcher/config/config-gold2-beta-vol-target-backtest.json`
- `Scripts/auto_optimize/evolution_scheduler.py`
- `Scripts/inspiration/generations.py` or `layer_state.py`
- LEAN core under `Common/`, `Algorithm/`, or `Engine/`
- existing `Results/gold2-betavol/` artifacts

Formal configs set `fallback-download-enabled=false`, `fallback-gbm-enabled=false`, and `influxdb-enabled=false`. Python never reconstructs fills, fees, holdings, cash, or portfolio value.

## Gate order

| Gate | Must pass | Stop result |
|---|---|---|
| P0 | At least 3 fixed eligible blind windows and static G3 readiness | `NOT_EVALUATED + UNPROVEN` |
| P1 | One-window isolated G0/G1, formal trace, proof/production equivalence | stop on any economic mismatch |
| P2 | Valid preregistration and at least 3 valid G0/G1/G2 windows | stop on residual attribution or review re-ranking |
| P3 | At least 3 windows with three valid proof-local generations | stop or remain unproven when unobservable |
| P4 | Global freeze before blind access, canonical statistics, complete seal | invalidate on mutation/hash mismatch |

---

## Phase 0 — feasibility before proof infrastructure

### Task 1: Fixed windows and minimal Phase 0 types

**Files:**
- Create: `Scripts/gold2_closed_loop/__init__.py`
- Create: `Scripts/gold2_closed_loop/phase0_types.py`
- Test: `Tests/gold2_closed_loop/test_phase0_types.py`

- [ ] **Step 1: Write the failing window test**

```python
from datetime import date
from Scripts.gold2_closed_loop.phase0_types import proof_windows


def test_fixed_windows_have_non_overlapping_blind_years():
    windows = proof_windows()
    assert [w.window_id for w in windows] == ["W1", "W2", "W3", "W4"]
    assert windows[0].train == (date(2018, 1, 2), date(2020, 12, 31))
    assert [w.blind[0].year for w in windows] == [2022, 2023, 2024, 2025]
    assert len({w.blind for w in windows}) == 4
```

- [ ] **Step 2: Verify it fails**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_phase0_types.py`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Add immutable records and exact W1–W4 definitions**

```python
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

class FeasibilityVerdict(StrEnum):
    PASS = "PASS"
    BLOCKED_INSUFFICIENT_TEST_WINDOWS = "BLOCKED_INSUFFICIENT_TEST_WINDOWS"
    BLOCKED_INSUFFICIENT_DATA = "BLOCKED_INSUFFICIENT_DATA"

@dataclass(frozen=True)
class WindowDefinition:
    window_id: str
    train: tuple[date, date]
    review: tuple[date, date]
    blind: tuple[date, date]
```

`proof_windows()` must return exactly the four date ranges in design §3; boundaries are not movable.

- [ ] **Step 4: Verify pass**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_phase0_types.py`

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop Tests/gold2_closed_loop/test_phase0_types.py
git commit -m "feat(gold2-proof): define fixed proof windows"
```

### Task 2: Read-only source inventory and proxy rejection

**Files:**
- Create: `Scripts/gold2_closed_loop/data_sources.py`
- Test: `Tests/gold2_closed_loop/test_data_sources.py`

- [ ] **Step 1: Write failing coverage/hash tests**

```python
import pandas as pd
import pytest
from Scripts.gold2_closed_loop.data_sources import inspect_source


def test_reports_dates_rows_duplicates_and_sha256(tmp_path):
    path = tmp_path / "daily.parquet"
    pd.DataFrame({"trade_date": ["20180102", "20180103"], "close": [2.7, 2.8]}).to_parquet(path)
    report = inspect_source("fund_daily", path, "trade_date")
    assert (report.first_date, report.last_date) == ("2018-01-02", "2018-01-03")
    assert report.row_count == report.distinct_date_count == 2
    assert report.duplicate_date_count == 0
    assert len(report.sha256) == 64


def test_rejects_nav_as_tradable_ohlc(tmp_path):
    path = tmp_path / "nav.parquet"
    pd.DataFrame({"nav_date": ["20180102"], "unit_nav": [1.0]}).to_parquet(path)
    with pytest.raises(ValueError, match="fund_nav cannot substitute"):
        inspect_source("fund_nav", path, "nav_date")
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_data_sources.py`

Expected: missing module.

- [ ] **Step 3: Implement read-only inspection**

Use a frozen `SourceReport` with logical name, absolute path, first/last ISO date, row count, distinct dates, duplicate dates, SHA-256, and annual counts. Stream file hashing in 1 MiB blocks. Read Parquet/CSV without writing. Reject `fund_nav`, pre-listing proxies, and non-OHLC 518880 inputs.

- [ ] **Step 4: Verify pass**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_data_sources.py`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/data_sources.py Tests/gold2_closed_loop/test_data_sources.py
git commit -m "feat(gold2-proof): audit immutable data sources"
```

### Task 3: Point-in-time feature availability and window gate

**Files:**
- Create: `Scripts/gold2_closed_loop/feature_availability.py`
- Create: `Scripts/gold2_closed_loop/feasibility.py`
- Test: `Tests/gold2_closed_loop/test_feature_availability.py`
- Test: `Tests/gold2_closed_loop/test_feasibility.py`

- [ ] **Step 1: Write failing no-lookahead/staleness tests**

```python
import pandas as pd
from Scripts.gold2_closed_loop.feature_availability import asof_available


def test_after_close_release_is_not_visible_same_day():
    sessions = pd.DatetimeIndex(["2024-01-02 15:00"], tz="Asia/Shanghai")
    releases = pd.DatetimeIndex(["2024-01-02 16:00"], tz="Asia/Shanghai")
    assert asof_available(sessions, releases, 5).tolist() == [False]


def test_release_expires_after_staleness_limit():
    sessions = pd.DatetimeIndex(["2024-01-10 15:00"], tz="Asia/Shanghai")
    releases = pd.DatetimeIndex(["2024-01-02 14:00"], tz="Asia/Shanghai")
    assert asof_available(sessions, releases, 5).tolist() == [False]
```

Also test that 3 eligible windows pass and 2 return `BLOCKED_INSUFFICIENT_TEST_WINDOWS`.

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_feature_availability.py Tests/gold2_closed_loop/test_feasibility.py`

Expected: missing modules.

- [ ] **Step 3: Implement backward as-of joins**

Use the China trading calendar as the session spine. For 518880, AU, VIX, and DFII10 apply draft-defined timezone, publication timestamp/lag, backward-only as-of join, and maximum staleness. Mark an entire fixed window invalid when an internal gap exceeds policy; never shift its boundary. Return `eligible_blind_window_count` and per-window reasons.

- [ ] **Step 4: Verify pass**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_feature_availability.py Tests/gold2_closed_loop/test_feasibility.py`

Expected: pass including exact-boundary cases.

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/feature_availability.py Scripts/gold2_closed_loop/feasibility.py Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): gate point-in-time window data"
```

### Task 4: Phase 0 CLI and artifacts

**Files:**
- Create: `Scripts/gold2_closed_loop/run_experiment.py`
- Create: `Scripts/gold2_closed_loop/config/gold2-proof-draft.example.yaml`
- Test: `Tests/gold2_closed_loop/test_feasibility_cli.py`

- [ ] **Step 1: Write a failing subprocess contract test**

```python
def test_assessment_writes_four_artifacts(tmp_path):
    result = run_cli(tmp_path, draft="experiment_id: test\nminimum_required_generation_count: 3\n")
    assert result.returncode == 2
    assert {p.name for p in (tmp_path / "audit").iterdir()} == {
        "data_coverage_audit.json", "window_inventory.json",
        "g3_observability.json", "interface_readiness.json",
    }
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_feasibility_cli.py`

Expected: CLI absent.

- [ ] **Step 3: Implement `assess-feasibility`**

```text
run_experiment.py assess-feasibility --input DRAFT --output AUDIT
```

Parse with `yaml.safe_load`; require path parameters instead of workstation constants. Call Tasks 2–3; write deterministic sorted JSON. `g3_observability.json` statically validates minimum 3 generations, budgets, failure policy, and required record fields. `interface_readiness.json` records known forbidden production interfaces. Do not run LEAN, create a sealed experiment root, or expose blind data. Return 0 on PASS and 2 on blocked.

- [ ] **Step 4: Run tests and real read-only assessment**

```bash
python -m pytest -q Tests/gold2_closed_loop/test_*feasibility*.py Tests/gold2_closed_loop/test_data_sources.py Tests/gold2_closed_loop/test_phase0_types.py
python Scripts/gold2_closed_loop/run_experiment.py assess-feasibility --input Scripts/gold2_closed_loop/config/gold2-proof-draft.example.yaml --output result/gold2-proof-feasibility
```

Expected: tests pass. A real exit 2 is a valid stopping outcome.

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): add Phase 0 feasibility gate"
```

**STOP P0:** If fewer than 3 fixed windows pass, write `NOT_EVALUATED + UNPROVEN` and stop. Do not shorten windows or substitute `fund_nav`/proxies.

---

## Phase 1 — one-window G0/G1 vertical slice and equivalence

### Task 5: Formal trace DTO, required sink, and reconciliation

**Files:**
- Create: `Algorithm.CSharp/Gold2ClosedLoop/IFormalTraceSink.cs`
- Create: `Algorithm.CSharp/Gold2ClosedLoop/FormalTraceEvent.cs`
- Create: `Algorithm.CSharp/Gold2ClosedLoop/FormalJsonlTraceSink.cs`
- Create: `Algorithm.CSharp/Gold2ClosedLoop/FormalTraceReconciler.cs`
- Test: `Tests/Algorithm/Gold2ClosedLoop/FormalTraceTests.cs`

- [ ] **Step 1: Write failing NUnit tests**

```csharp
[Test]
public void FillPreservesDecimals()
{
    var value = new FormalTraceEvent("1", 1, "FILL", "exp", "W1", "G0", "run", "c", DateTime.UtcNow,
        new { orderId = 7, fillPrice = 12.34m, fillQuantity = 100m, fee = 1.23m });
    var json = JsonConvert.SerializeObject(value);
    Assert.That(json, Does.Contain("12.34"));
}

[Test]
public void SinkCreationFailurePropagates()
{
    Assert.Throws<IOException>(() => new FormalJsonlTraceSink("/missing/trace.jsonl"));
}
```

- [ ] **Step 2: Verify compile failure**

Run: `dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~FormalTraceTests"`

- [ ] **Step 3: Implement strict writer**

`IFormalTraceSink` exposes `Write`, `FlushAndReconcile`, and `Dispose`. Open with `FileMode.CreateNew`; serialize under a lock; require `sequence=last+1`; call writer flush and `FileStream.Flush(true)`; never swallow I/O errors. Reconciler requires every FILL to reference an ORDER_INTENT and have a following correlated HOLDINGS_SNAPSHOT.

- [ ] **Step 4: Verify pass**

Run: `dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2ClosedLoop" --logger "console;verbosity=detailed"`

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/Gold2ClosedLoop Tests/Algorithm/Gold2ClosedLoop
git commit -m "feat(gold2-proof): add formal trace sink"
```

### Task 6: Proof-only subclass and execution decorator

**Files:**
- Create: `Algorithm.CSharp/Gold2ClosedLoopProofStrategy.cs`
- Create: `Algorithm.CSharp/Gold2ClosedLoop/TracingExecutionModel.cs`
- Test: `Tests/Algorithm/Gold2ClosedLoop/Gold2ProofInstrumentationTests.cs`

- [ ] **Step 1: Write a failing inheritance/delegation test**

```csharp
[Test]
public void ProofStrategyInheritsMatureGold2()
{
    Assert.That(typeof(Gold2ClosedLoopProofStrategy).BaseType,
        Is.EqualTo(typeof(Gold2BetaVolTargetStrategy)));
    Assert.That(typeof(TracingExecutionModel).GetField("_inner",
        BindingFlags.NonPublic | BindingFlags.Instance), Is.Not.Null);
}
```

- [ ] **Step 2: Verify compile failure**

Run: `dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2ProofInstrumentationTests"`

- [ ] **Step 3: Implement subclass-and-decorate**

```csharp
public override void Initialize()
{
    base.Initialize();
    var path = GetParameter("formal-trace-path");
    if (string.IsNullOrWhiteSpace(path)) throw new ArgumentException("formal-trace-path is required");
    _sink = new FormalJsonlTraceSink(path);
    Execution = new TracingExecutionModel(Execution, _sink, this);
}
```

The decorator receives final risk-adjusted targets, emits DECISION, delegates once to the original `AShareLotSizeExecutionModel`, then detects newly created native tickets/orders and emits actual ORDER_INTENT. Do not reproduce order sizing, lot rounding, or margin checks. `OnOrderEvent` emits native FILL then immediate post-fill HOLDINGS_SNAPSHOT. `OnEndOfAlgorithm` reconciles and flushes; failure becomes runtime failure.

- [ ] **Step 4: Run all Gold2 C# tests**

Run: `dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2" --logger "console;verbosity=detailed"`

Expected: existing and proof tests pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/Gold2ClosedLoopProofStrategy.cs Algorithm.CSharp/Gold2ClosedLoop Tests/Algorithm/Gold2ClosedLoop
git commit -m "feat(gold2-proof): instrument proof-only Gold2"
```

### Task 7: Isolated LEAN runner and exact packets

**Files:**
- Create: `Scripts/gold2_closed_loop/lean_runner.py`
- Create: `Scripts/gold2_closed_loop/lean_artifacts.py`
- Create: `Scripts/gold2_closed_loop/config/proof_lean_base.json`
- Test: `Tests/gold2_closed_loop/test_lean_runner.py`

- [ ] **Step 1: Write failing isolation tests**

```python
def test_config_disables_fallbacks_and_sets_absolute_run_dir(tmp_path):
    config = build_run_config(BASE, tmp_path / "run", "Gold2ClosedLoopProofStrategy", {})
    assert config["results-destination-folder"] == str((tmp_path / "run").resolve())
    assert config["fallback-download-enabled"] is False
    assert config["fallback-gbm-enabled"] is False
    assert config["influxdb-enabled"] is False


def test_exact_packet_required(tmp_path):
    (tmp_path / "stale.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="expected result packet"):
        load_exact_packet(tmp_path, "run-001")
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_lean_runner.py`

- [ ] **Step 3: Implement typed execution**

Define frozen `LeanExecutionResult` with status, exit code, paths/hashes, timing, and error. Deep-copy an independent base config; inject exact assembly/type, dates, parameters, IDs, trace path, absolute unique `run_dir`, and disabled side effects. Invoke explicit dotnet/launcher with timeout and exact expected `<run_id>.json`; never glob or select by mtime. Classify trace failures as `FAILED_EVIDENCE_CAPTURE`.

- [ ] **Step 4: Verify success/error cases**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_lean_runner.py`

Expected: pass for success, timeout, nonzero exit, missing/stale packet, and concurrent directories.

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/lean_runner.py Scripts/gold2_closed_loop/lean_artifacts.py Scripts/gold2_closed_loop/config/proof_lean_base.json Tests/gold2_closed_loop/test_lean_runner.py
git commit -m "feat(gold2-proof): isolate LEAN runs"
```

### Task 8: Strict loader and behavioral equivalence

**Files:**
- Create: `Scripts/gold2_closed_loop/formal_trace.py`
- Create: `Scripts/gold2_closed_loop/equivalence.py`
- Test: `Tests/gold2_closed_loop/test_formal_trace.py`
- Test: `Tests/gold2_closed_loop/test_gold2_equivalence.py`
- Test: `Tests/gold2_closed_loop/test_gold2_lean_integration.py`

- [ ] **Step 1: Write failing rejection/mismatch tests**

```python
def test_rl_state_is_not_formal():
    with pytest.raises(ValueError, match="unknown formal event"):
        validate_formal_events([{"time": "2022-01-04", "tpv": 1000000}])


def test_decimal_price_mismatch_blocks_equivalence():
    left = [{"event_type": "FILL", "time": "2022-01-04T07:00:00Z", "quantity": "100", "price": "3.50"}]
    right = [{"event_type": "FILL", "time": "2022-01-04T07:00:00Z", "quantity": "100", "price": "3.51"}]
    assert not compare_economic_events(left, right, 1e-12, 1e-12).equivalent
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_formal_trace.py Tests/gold2_closed_loop/test_gold2_equivalence.py`

- [ ] **Step 3: Implement strict validation/comparison**

Require known schema/event, full identity tuple, increasing sequence, nondecreasing UTC time, finite values, matching hashes, and intent→fill→holdings reconciliation. Compare multiplicity/order/time and exact `Decimal` quantities, prices, fees, cash, holdings, and TPV. Only derived double weights use preregistered tolerances; ignore only proof IDs, output paths, and serialization formatting.

- [ ] **Step 4: Run unit and opt-in real integration**

```bash
python -m pytest -q Tests/gold2_closed_loop/test_formal_trace.py Tests/gold2_closed_loop/test_gold2_equivalence.py
GOLD2_PROOF_INTEGRATION=1 python -m pytest -q Tests/gold2_closed_loop/test_gold2_lean_integration.py
```

Expected: unit tests pass; enabled integration compares mature G0 and proof G0 on identical W1 data and fails on any economic mismatch.

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/formal_trace.py Scripts/gold2_closed_loop/equivalence.py Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): enforce Gold2 equivalence"
```

**STOP P1:** Continue only after a real one-window G0/small-budget G1 produces exact artifacts, complete trace, and zero economic mismatch.

---

## Phase 2 — formal lifecycle and four-window G0/G1/G2

### Task 9: Schemas, state machine, atomic I/O, and journal

**Files:**
- Create: `Scripts/gold2_closed_loop/schemas.py`
- Create: `Scripts/gold2_closed_loop/schema/preregistration.schema.json`
- Create: `Scripts/gold2_closed_loop/schema/candidate-event.schema.json`
- Create: `Scripts/gold2_closed_loop/atomic_io.py`
- Create: `Scripts/gold2_closed_loop/event_journal.py`
- Create: `Scripts/gold2_closed_loop/state_machine.py`
- Create: `Scripts/gold2_closed_loop/evidence.py`
- Test: `Tests/gold2_closed_loop/test_schemas.py`
- Test: `Tests/gold2_closed_loop/test_state_machine.py`
- Test: `Tests/gold2_closed_loop/test_event_journal.py`

- [ ] **Step 1: Write failing verdict/hash-chain tests**

```python
def test_disposition_pairs_are_closed():
    assert validate_verdict("NOT_EVALUATED", "UNPROVEN")
    with pytest.raises(ValueError):
        validate_verdict("NOT_EFFECTIVE", "UNPROVEN")


def test_previous_hash_mismatch_is_rejected(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    journal.append("CANDIDATE_REGISTERED", {"candidate_id": "c1"})
    with pytest.raises(ValueError, match="previous hash"):
        journal.append_raw({"sequence": 2, "previous_event_sha256": "0" * 64})
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_schemas.py Tests/gold2_closed_loop/test_state_machine.py Tests/gold2_closed_loop/test_event_journal.py`

- [ ] **Step 3: Implement exact design enums and durable writes**

Include lifecycle, validity, invalid/block/execution reason, verdict/disposition, candidate event/state, convergence, generation terminal, and G3 alias enums. Preregistration schema requires every numeric threshold in design §13, finite values, windows, seeds, budgets, retry policy, metric paths, as-of/staleness, and equivalence tolerances. Atomic JSON uses unique same-directory temp, flush/fsync, `os.replace`, directory fsync. Journal takes an exclusive lock, rejects duplicate IDs, increments sequence, and hashes canonical JSON with previous hash.

- [ ] **Step 4: Verify pass including concurrency/interruption**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_schemas.py Tests/gold2_closed_loop/test_state_machine.py Tests/gold2_closed_loop/test_event_journal.py`

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): add evidence lifecycle"
```

### Task 10: Proof-local G1 optimizer with complete attempt accounting

**Files:**
- Create: `Scripts/gold2_closed_loop/adapters/__init__.py`
- Create: `Scripts/gold2_closed_loop/adapters/base.py`
- Create: `Scripts/gold2_closed_loop/adapters/parameter_optimizer.py`
- Create: `Scripts/gold2_closed_loop/candidate_registry.py`
- Create: `Scripts/gold2_closed_loop/selection.py`
- Test: `Tests/gold2_closed_loop/test_parameter_optimizer_adapter.py`

- [ ] **Step 1: Write failing typing/budget test**

```python
def test_bool_and_failure_both_consume_budget(fake_runner, tmp_path):
    fake_runner.results = [TimeoutError(), {"sharpe": .7, "net_profit": .1, "mdd": .08}]
    result = ParameterOptimizerAdapter(fake_runner, 2, 17, tmp_path / "events.jsonl").run(
        {"trend-disable": {"type": "bool"}}, "W1/train")
    assert result.attempted_trial_count == 2
    assert isinstance(result.attempts[1].parameters["trend-disable"], bool)
    assert [a.status for a in result.attempts] == ["TIMED_OUT", "SUCCEEDED"]
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_parameter_optimizer_adapter.py`

- [ ] **Step 3: Implement adapter and selection**

Typed request contains all experiment/window/stage/candidate/partition/snapshot/trace/run-dir/observation/shaping/seed/budget fields. Register before execution. Every success, strategy/infrastructure failure, timeout, no-trade, prune, duplicate, dominated, rejected, or invalid attempt consumes budget. Select only candidates passing minimum trades, MDD, DSR, bounds, and train-subwindow stability using preregistered ranking/tie-break. No candidate aliases G1 to G0.

- [ ] **Step 4: Verify all outcomes**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_parameter_optimizer_adapter.py`

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/adapters Scripts/gold2_closed_loop/candidate_registry.py Scripts/gold2_closed_loop/selection.py Tests/gold2_closed_loop/test_parameter_optimizer_adapter.py
git commit -m "feat(gold2-proof): add G1 optimization"
```

### Task 11: Formal review and G2 feedback

**Files:**
- Create: `Scripts/gold2_closed_loop/adapters/formal_review.py`
- Create: `Scripts/gold2_closed_loop/adapters/feedback_construction.py`
- Test: `Tests/gold2_closed_loop/test_formal_review_adapter.py`
- Test: `Tests/gold2_closed_loop/test_feedback_construction_adapter.py`

- [ ] **Step 1: Write failing no-residual/no-re-ranking tests**

```python
def test_missing_trace_invalidates_review(tmp_path):
    result = FormalReviewAdapter().run(tmp_path / "missing.jsonl")
    assert (result.validity_status, result.invalid_reason) == ("INVALID", "MISSING_FORMAL_TRACE")


def test_review_partition_never_ranks_g2(fake_runner, frozen_bundle):
    FeedbackConstructionAdapter(fake_runner).run(frozen_bundle, "W1/train", "W1/review")
    assert {call.partition for call in fake_runner.calls} == {"W1/train"}
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_formal_review_adapter.py Tests/gold2_closed_loop/test_feedback_construction_adapter.py`

- [ ] **Step 3: Implement one immutable bundle and train-only G2**

Derive realized weight only from post-fill holdings; require complete telescoping attribution; write one canonical bundle/hash. Missing/incomplete formal trace invalidates; no residual fallback. Frozen review may choose allowed shaping/bound changes once; all G2 objectives run on train only. Never import production scheduler/state.

- [ ] **Step 4: Verify formal and existing diagnostic tests**

```bash
python -m pytest -q Tests/gold2_closed_loop/test_formal_review_adapter.py Tests/gold2_closed_loop/test_feedback_construction_adapter.py
python -m pytest -q Tests/test_review_gold2_attribution.py Tests/test_feedback_gold2.py Tests/test_feedback_e2e.py
```

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/adapters Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): add frozen review feedback"
```

### Task 12: Four-window runner and readiness recheck

**Files:**
- Create: `Scripts/gold2_closed_loop/proof_runner.py`
- Create: `Scripts/gold2_closed_loop/interface_readiness.py`
- Modify: `Scripts/gold2_closed_loop/run_experiment.py`
- Test: `Tests/gold2_closed_loop/test_proof_runner.py`
- Test: `Tests/gold2_closed_loop/test_interface_readiness.py`

- [ ] **Step 1: Write failing stage-order/forbidden-reference tests**

```python
def test_all_windows_construct_before_blind(fake_adapters):
    ProofRunner(fake_adapters).construct(["W1", "W2", "W3", "W4"])
    assert "BLIND_ACCESS_OPENED" not in fake_adapters.events


def test_readiness_rejects_daemon_import(tmp_path):
    (tmp_path / "bad.py").write_text("import evolution_scheduler\n")
    assert "evolution_scheduler" in inspect_proof_interfaces(tmp_path).forbidden_references
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_proof_runner.py Tests/gold2_closed_loop/test_interface_readiness.py`

- [ ] **Step 3: Implement construction and preregistration**

Construct W1–W4 G0, G1, one review, and G2 with blind absent. Readiness rejects production daemon/evolution/generation/layer state, global Results glob, OptionVolArb path, hard-coded dimensions, writable blind mount, and enabled fallbacks. `preregister` requires P0/P1/readiness PASS, validates schema, rejects existing ID, snapshots/hashes inputs, and atomically creates `PREREGISTERED`.

- [ ] **Step 4: Run Phase 2 suite**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_schemas.py Tests/gold2_closed_loop/test_state_machine.py Tests/gold2_closed_loop/test_event_journal.py Tests/gold2_closed_loop/test_parameter_optimizer_adapter.py Tests/gold2_closed_loop/test_formal_review_adapter.py Tests/gold2_closed_loop/test_feedback_construction_adapter.py Tests/gold2_closed_loop/test_proof_runner.py Tests/gold2_closed_loop/test_interface_readiness.py`

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): construct four-window G0 G1 G2"
```

**STOP P2:** Require at least 3 valid formal G0/G1/G2 windows. Stop on residual attribution, review re-ranking, or readiness failure.

---

## Phase 3 — G3 only when observable

### Task 13: Partition sandbox, generation journal, and trigger

**Files:**
- Create: `Scripts/gold2_closed_loop/partition_sandbox.py`
- Create: `Scripts/gold2_closed_loop/generation_journal.py`
- Create: `Scripts/gold2_closed_loop/g3_trigger.py`
- Test: `Tests/gold2_closed_loop/test_partition_sandbox.py`
- Test: `Tests/gold2_closed_loop/test_generation_journal.py`
- Test: `Tests/gold2_closed_loop/test_g3_trigger.py`

- [ ] **Step 1: Write failing blind-absence/boundary tests**

```python
def test_construction_mounts_exclude_blind(tmp_path):
    mounts = build_construction_mounts(tmp_path/"train", tmp_path/"review", tmp_path/"blind", tmp_path/"run")
    assert all("blind" not in str(m.source) for m in mounts)


def test_three_valid_generations_trigger_inclusively():
    records = [generation(i, gap=.2, weight=3.0, pending=0, evidence_hash="a"*64) for i in (1,2,3)]
    assert evaluate_g3(records, .2, 3.0).eligible
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_partition_sandbox.py Tests/gold2_closed_loop/test_generation_journal.py Tests/gold2_closed_loop/test_g3_trigger.py`

- [ ] **Step 3: Implement enforceable isolation and closed semantics**

Train/review mounts are read-only, run writable, blind absent, access logged. Generation records are append-only, parent-linked, collision-rejecting, and contain all design fields. Trigger requires three adjacent valid records, identical evidence hash, `gap >= threshold`, weight `>= ceiling` or NON_CONVERGED, and zero pending candidates. Interrupted evidence produces `TRIGGER_UNOBSERVABLE`; sufficient non-trigger evidence produces `NOT_TRIGGERED`.

- [ ] **Step 4: Verify all alias/boundary cases**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_partition_sandbox.py Tests/gold2_closed_loop/test_generation_journal.py Tests/gold2_closed_loop/test_g3_trigger.py`

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): enforce G3 observability"
```

### Task 14: Isolated G3 construction

**Files:**
- Create: `Scripts/gold2_closed_loop/adapters/inspiration_construction.py`
- Create: `Scripts/gold2_closed_loop/g3_builder.py`
- Test: `Tests/gold2_closed_loop/test_inspiration_construction_adapter.py`

- [ ] **Step 1: Write failing frozen-source/failure tests**

```python
def test_builder_freezes_generated_source(fake_generator, tmp_path):
    result = G3Builder(fake_generator).build(request(), tmp_path / "proof")
    assert result.source_sha256

@pytest.mark.parametrize("failure,reason", [
    ("generation", "CONSTRUCTION_FAILED"),
    ("compile", "CONSTRUCTION_FAILED"),
    ("training_gate", "CANDIDATE_REJECTED"),
])
def test_closed_failure_reason(failing_builder, failure, reason):
    assert failing_builder(failure).alias_reason == reason
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_inspiration_construction_adapter.py`

- [ ] **Step 3: Implement injected generator/build contract**

Consume only frozen evidence and window-local generations. Freeze request/response/source/compiler/config hashes. Build in an isolated candidate directory; never overwrite mature Gold2/production state. Register generation, validation, compile, strategy, no-trade, and training-gate outcomes; all consume budget. Rejection reuses G2 hash and caps verdict at PARTIALLY_EFFECTIVE.

- [ ] **Step 4: Verify tests and readiness**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_inspiration_construction_adapter.py Tests/gold2_closed_loop/test_interface_readiness.py`

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/adapters/inspiration_construction.py Scripts/gold2_closed_loop/g3_builder.py Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): add isolated G3 construction"
```

**STOP P3:** At least 3 final valid windows need complete G3 observability. `NOT_TRIGGERED` means redesign not needed, never redesign success.

---

## Phase 4 — freeze, blind evaluation, statistics, and seal

### Task 15: Global freeze and blind evaluator

**Files:**
- Create: `Scripts/gold2_closed_loop/blind_evaluator.py`
- Test: `Tests/gold2_closed_loop/test_blind_evaluator.py`
- Test: `Tests/gold2_closed_loop/test_resume.py`

- [ ] **Step 1: Write failing freeze/mutation tests**

```python
def test_blind_requires_every_eligible_window(evaluator):
    evaluator.freeze("W1", candidates("W1"))
    with pytest.raises(RuntimeError, match="global candidate freeze incomplete"):
        evaluator.open_blind(["W1", "W2", "W3"])


def test_post_open_mutation_invalidates(evaluator):
    evaluator.freeze_all(frozen_sets())
    evaluator.open_blind(["W1", "W2", "W3", "W4"])
    evaluator.code_path.write_text("mutation")
    assert evaluator.validate_immutability().invalid_reason == "POST_BLIND_MUTATION"
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_blind_evaluator.py Tests/gold2_closed_loop/test_resume.py`

- [ ] **Step 3: Implement freeze/open/evaluate**

Freeze every eligible window/stage identity/hash, then atomically write `blind_opened.json`, expose blind only to evaluator, revalidate all P0/data/code/config/assembly/model hashes, and execute exact candidates. After opening reject generation, repair, retry, reselection, config change, and normal resume. Pre-open resume skips only complete hash-valid operation IDs; quarantine partial directories.

- [ ] **Step 4: Verify pass**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_blind_evaluator.py Tests/gold2_closed_loop/test_resume.py`

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/blind_evaluator.py Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): freeze before blind evaluation"
```

### Task 16: Canonical metrics, clustered inference, and verdict

**Files:**
- Create: `Scripts/gold2_closed_loop/metric_registry.py`
- Create: `Scripts/gold2_closed_loop/statistical_tests.py`
- Create: `Scripts/gold2_closed_loop/verdict.py`
- Test: `Tests/gold2_closed_loop/test_metric_registry.py`
- Test: `Tests/gold2_closed_loop/test_statistical_tests.py`
- Test: `Tests/gold2_closed_loop/test_verdict.py`

- [ ] **Step 1: Write failing canonical/verdict tests**

```python
def test_sharpe_comes_from_portfolio_statistics(packet):
    packet["totalPerformance"]["portfolioStatistics"]["sharpeRatio"] = .6
    packet["totalPerformance"]["tradeStatistics"]["sharpeRatio"] = 9.0
    assert extract_acceptance_metrics(packet)["sharpe"] == .6


def test_valid_negative_is_refuted():
    result = decide_verdict(validity="VALID", aggregate_delta=-.01)
    assert (result.verdict, result.disposition) == ("NOT_EFFECTIVE", "REFUTED")


def test_missing_trace_is_unproven():
    result = decide_verdict(validity="INVALID", invalid_reason="MISSING_FORMAL_TRACE")
    assert (result.verdict, result.disposition) == ("NOT_EVALUATED", "UNPROVEN")
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_metric_registry.py Tests/gold2_closed_loop/test_statistical_tests.py Tests/gold2_closed_loop/test_verdict.py`

- [ ] **Step 3: Implement canonical registry and inference**

Acceptance paths:

```text
totalPerformance.portfolioStatistics.totalNetProfit
totalPerformance.portfolioStatistics.compoundingAnnualReturn
totalPerformance.portfolioStatistics.sharpeRatio
totalPerformance.portfolioStatistics.drawdown
totalPerformance.portfolioStatistics.informationRatio
statistics.Total Fees (reconciled with native evidence)
```

Validate benchmark chart before IR. Use native equity/return/benchmark/drawdown series. Run seeded paired moving-block bootstrap within windows; cluster aggregate by annual window; compute DSR/effective trials from every attempt; report correlations, every leave-one-window-out, and trade/month concentration. Implement all §13–14 stage, 3/4 or 2/3, MDD, G3 alias, and disposition rules.

- [ ] **Step 4: Verify statistical boundary cases**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_metric_registry.py Tests/gold2_closed_loop/test_statistical_tests.py Tests/gold2_closed_loop/test_verdict.py`

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/metric_registry.py Scripts/gold2_closed_loop/statistical_tests.py Scripts/gold2_closed_loop/verdict.py Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): compute economic verdict"
```

### Task 17: Complete evidence seal, report, and CLI

**Files:**
- Create: `Scripts/gold2_closed_loop/sealing.py`
- Create: `Scripts/gold2_closed_loop/report.py`
- Modify: `Scripts/gold2_closed_loop/run_experiment.py`
- Test: `Tests/gold2_closed_loop/test_sealing.py`
- Test: `Tests/gold2_closed_loop/test_end_to_end.py`

- [ ] **Step 1: Write failing completeness/mutation tests**

```python
def test_seal_refuses_missing_failed_candidate(evidence_tree):
    with pytest.raises(ValueError, match="failed candidate artifact missing"):
        build_seal(evidence_tree, FakeSigner(), FakePublisher())


def test_mutation_breaks_seal(evidence_tree):
    seal = build_seal(evidence_tree, FakeSigner(), FakePublisher())
    (evidence_tree / "aggregate" / "verdict.json").write_text("{}")
    assert not verify_seal(evidence_tree, seal)
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest -q Tests/gold2_closed_loop/test_sealing.py Tests/gold2_closed_loop/test_end_to_end.py`

- [ ] **Step 3: Implement deterministic seal/report and final CLI**

Index every preregistration, feasibility artifact, snapshot, code/config/data/assembly/model hash, candidate including failures, generation, LEAN packet, trace, statistic, and verdict. Use an injected external signer key reference and append-only publisher; record root hash, algorithm, public-key fingerprint, trusted time, and receipt. Generate report only from indexed artifacts, including all windows/stages/deltas, baselines, worst window, concentration, failures, DSR/bootstrap/correlation/leave-one-out, verdict, and hashes.

Final CLI:

```text
assess-feasibility --input DRAFT --output AUDIT
preregister --input DRAFT --experiment-id ID
execute --experiment-root ROOT [--resume]
verify-seal --experiment-root ROOT
```

Each command enforces lifecycle and phase gates; stopped/negative runs retain all evidence.

- [ ] **Step 4: Run complete verification**

```bash
python -m pytest -q Tests/gold2_closed_loop
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2ClosedLoop" --logger "console;verbosity=detailed"
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2" --logger "console;verbosity=detailed"
python -m pytest -q Tests/test_review_gold2_attribution.py Tests/test_feedback_gold2.py Tests/test_inspiration_trigger.py
dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj
dotnet build Launcher/QuantConnect.Lean.Launcher.csproj
```

Expected: all pass. Integration tests may skip only without explicit opt-in; evidence-producing execution may not skip.

Run formal CLI in order with absolute paths. `verify-seal` must report the same published root hash and receipt.

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop Tests/gold2_closed_loop
git commit -m "feat(gold2-proof): seal closed-loop evidence"
```

---

## Final acceptance checklist

- [ ] Phase 0 used only real tradable 518880 OHLC and exact source hashes.
- [ ] W1–W4 boundaries stayed fixed; at least 3 blind years are eligible.
- [ ] Mature Gold2, production manifest/config/daemon/state, and LEAN core have no diff.
- [ ] Proof G0 is economically equivalent event-by-event to mature G0.
- [ ] Every attempt, including failures, consumed budget and is hash-chained.
- [ ] Formal review uses post-fill holdings and complete telescoping attribution; residual fallback is unreachable.
- [ ] Construction could not see blind partitions; mounts/access logs are sealed.
- [ ] All eligible windows/stages froze before `BLIND_ACCESS_OPENED`.
- [ ] No generation, retry, repair, or reselection occurred after blind opening.
- [ ] Acceptance metrics came from canonical LEAN paths; Python did not reconstruct accounting.
- [ ] Verdict distinguishes EFFECTIVE, PARTIALLY_EFFECTIVE, NOT_EFFECTIVE+REFUTED, and NOT_EVALUATED+UNPROVEN.
- [ ] NOT_TRIGGERED is reported as redesign not needed, never redesign success.
- [ ] Failed candidates, worst windows, and negative stages are included before sealing.
- [ ] `verify-seal` matches the external append-only publication receipt.
