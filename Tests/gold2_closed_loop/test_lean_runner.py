"""Tests for the isolated LEAN proof runner (Task 7).

These tests NEVER invoke real LEAN. They exercise build_run_config /
load_exact_packet / classify_outcome / run_lean with fakes (monkeypatched
subprocess.run).
"""

from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from Scripts.gold2_closed_loop.lean_artifacts import (
    FAILED_EVIDENCE_CAPTURE,
    FAILED_MISSING_PACKET,
    FAILED_NONZERO_EXIT,
    FAILED_TIMEOUT,
    SUCCEEDED,
    LeanExecutionResult,
    classify_outcome,
    load_exact_packet,
    sha256_file,
)
from Scripts.gold2_closed_loop.lean_runner import (
    build_run_config,
    run_lean,
)

BASE = {
    "environment": "backtesting",
    "algorithm-type-name": "Gold2BetaVolTargetStrategy",
    "algorithm-language": "CSharp",
    "algorithm-location": "../../../Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll",
    "data-folder": "../../../Data",
    "data-directory": "../../../Data",
    "history-provider": "FallbackTushareHistoryProvider",
    "data-provider": "DefaultDataProvider",
    "results-destination-folder": "../../../Results/gold2-betavol",
    "influxdb-enabled": "true",
    "influxdb-url": "http://127.0.0.1:8086",
    "influxdb-org": "lean",
    "influxdb-bucket": "quant",
    "influxdb-token-env-var": "INFLUXDB_TOKEN",
    "parameters": {
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data_v2",
        "start-date": "2020-01-01",
        "end-date": "2026-06-23",
        "vol-target": "0.11",
        "initial-capital": "1000000",
    },
}


# --- Plan contract tests ---------------------------------------------------


def test_config_disables_fallbacks_and_sets_absolute_run_dir(tmp_path):
    config = build_run_config(
        BASE, tmp_path / "run", "Gold2ClosedLoopProofStrategy", {}, run_id="run-001"
    )
    assert config["results-destination-folder"] == str((tmp_path / "run").resolve())
    # influxdb-enabled is a top-level config key (InfluxDbResultExporter reads
    # it via Config.Get), so it is forced False at the top level.
    assert config["influxdb-enabled"] is False
    # CRITICAL: fallback-download-enabled / fallback-gbm-enabled are read from
    # parameters.Job.Parameters (FallbackTushareHistoryProvider.cs:66-67), NOT
    # top-level config. They MUST live in the `parameters` dict as strings
    # (Job.Parameters is Dictionary<string,string>), or the fallbacks stay
    # silently ACTIVE (default true) and a missing symbol triggers a live
    # download / synthetic GBM data — a silent false proof. There must be NO
    # top-level copy (it would be inert dead weight invisible to the engine).
    assert "fallback-download-enabled" not in config
    assert "fallback-gbm-enabled" not in config
    assert config["parameters"]["fallback-download-enabled"] == "false"
    assert config["parameters"]["fallback-gbm-enabled"] == "false"


def test_exact_packet_required(tmp_path):
    (tmp_path / "stale.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="expected result packet"):
        load_exact_packet(tmp_path, "run-001")


# --- deepcopy isolation ----------------------------------------------------


def test_build_run_config_does_not_mutate_base(tmp_path):
    base_snapshot = json.dumps(BASE, sort_keys=True)
    build_run_config(
        BASE, tmp_path / "run", "Gold2ClosedLoopProofStrategy", {}, run_id="r1"
    )
    assert json.dumps(BASE, sort_keys=True) == base_snapshot


def test_mutating_returned_config_does_not_affect_base(tmp_path):
    config = build_run_config(
        BASE, tmp_path / "run", "Gold2ClosedLoopProofStrategy", {}, run_id="r1"
    )
    config["parameters"]["tainted"] = "yes"
    config["influxdb-enabled"] = "true"
    assert BASE.get("tainted") is None
    assert BASE["influxdb-enabled"] == "true"  # base untouched by the mutation
    assert "tainted" not in BASE["parameters"]


# --- formal-trace-path + identity injection --------------------------------


def test_formal_trace_path_injected_when_trace_given(tmp_path):
    trace = tmp_path / "trace.jsonl"
    config = build_run_config(
        BASE,
        tmp_path / "run",
        "Gold2ClosedLoopProofStrategy",
        {},
        run_id="r1",
        trace_path=trace,
    )
    assert config["parameters"]["formal-trace-path"] == str(trace.resolve())


def test_formal_trace_path_optional_at_build_time(tmp_path):
    # build_run_config does not enforce the proof's runtime trace requirement;
    # it simply does not inject formal-trace-path when trace_path is absent.
    config = build_run_config(
        BASE,
        tmp_path / "run",
        "Gold2ClosedLoopProofStrategy",
        {},
        run_id="r1",
    )
    assert "formal-trace-path" not in config["parameters"]


def test_formal_trace_path_passthrough_via_parameters(tmp_path):
    trace = tmp_path / "trace.jsonl"
    config = build_run_config(
        BASE,
        tmp_path / "run",
        "Gold2ClosedLoopProofStrategy",
        {"formal-trace-path": str(trace)},
        run_id="r1",
    )
    assert config["parameters"]["formal-trace-path"] == str(trace)


def test_identity_params_injected(tmp_path):
    config = build_run_config(
        BASE,
        tmp_path / "run",
        "Gold2ClosedLoopProofStrategy",
        {},
        run_id="run-001",
        experiment_id="E1",
        window_id="W1",
        stage_id="G0",
        candidate_id="C1",
    )
    params = config["parameters"]
    assert params["experiment-id"] == "E1"
    assert params["window-id"] == "W1"
    assert params["stage-id"] == "G0"
    assert params["run-id"] == "run-001"
    assert params["candidate-id"] == "C1"


def test_run_id_defaults_in_parameters(tmp_path):
    config = build_run_config(
        BASE,
        tmp_path / "run",
        "Gold2ClosedLoopProofStrategy",
        {},
        run_id="abc-123",
    )
    assert config["parameters"]["run-id"] == "abc-123"


# --- forced fields ---------------------------------------------------------


def test_forced_fields_override_base(tmp_path):
    base = {**BASE, "fallback-download-enabled": "true", "fallback-gbm-enabled": "true"}
    config = build_run_config(
        base, tmp_path / "run", "Gold2ClosedLoopProofStrategy", {}, run_id="r1"
    )
    assert config["environment"] == "backtesting"
    assert config["algorithm-language"] == "CSharp"
    assert config["algorithm-type-name"] == "Gold2ClosedLoopProofStrategy"
    assert config["influxdb-enabled"] is False
    # Stale top-level fallback keys (even if base set them true) are removed;
    # the engine never reads top-level fallback keys, so leaving them would be
    # inert dead weight. The live enforcement lives in `parameters` as "false".
    assert "fallback-download-enabled" not in config
    assert "fallback-gbm-enabled" not in config
    assert config["parameters"]["fallback-download-enabled"] == "false"
    assert config["parameters"]["fallback-gbm-enabled"] == "false"
    # A base that tried to enable fallbacks via the parameters dict is also
    # overridden by the runner's forced "false".
    base2 = copy.deepcopy(BASE)
    base2.setdefault("parameters", {})["fallback-download-enabled"] = "true"
    config2 = build_run_config(
        base2, tmp_path / "run2", "Gold2ClosedLoopProofStrategy", {}, run_id="r2"
    )
    assert config2["parameters"]["fallback-download-enabled"] == "false"


def test_results_destination_folder_absolute_and_resolved(tmp_path):
    run_dir = tmp_path / "nested" / "run"
    config = build_run_config(
        BASE, run_dir, "Gold2ClosedLoopProofStrategy", {}, run_id="r1"
    )
    rdir = Path(config["results-destination-folder"])
    assert rdir.is_absolute()
    assert rdir == run_dir.resolve()


def test_algorithm_id_set_to_run_id(tmp_path):
    config = build_run_config(
        BASE, tmp_path / "run", "Gold2ClosedLoopProofStrategy", {}, run_id="run-xyz"
    )
    assert config["algorithm-id"] == "run-xyz"


def test_dates_and_tunables_merged(tmp_path):
    config = build_run_config(
        BASE,
        tmp_path / "run",
        "Gold2ClosedLoopProofStrategy",
        {"vol-target": "0.15"},
        run_id="r1",
        start_date="2022-01-01",
        end_date="2023-12-31",
    )
    params = config["parameters"]
    assert params["start-date"] == "2022-01-01"
    assert params["end-date"] == "2023-12-31"
    assert params["vol-target"] == "0.15"
    assert params["tushare-data-path"] == "/home/project/tushare-downloader/tushare_data_v2"


# --- atomic config write ---------------------------------------------------


def test_config_written_atomically(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = tmp_path / "trace.jsonl"
    config = build_run_config(
        BASE, run_dir, "Gold2ClosedLoopProofStrategy", {}, run_id="r1", trace_path=trace
    )

    def fake_run(cmd, *args, **kwargs):
        (run_dir / "r1.json").write_text(json.dumps({"ok": True}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_lean(
        config,
        run_dir=run_dir,
        run_id="r1",
        timeout_seconds=60,
        worktree_root=tmp_path,
        trace_path=trace,
    )
    assert result.status == SUCCEEDED
    config_path = Path(result.config_path)
    assert config_path.is_file()
    loaded = json.loads(config_path.read_text())
    assert loaded["algorithm-id"] == "r1"
    # No tmp files left behind.
    leftovers = [p.name for p in run_dir.iterdir() if p.name.startswith(".config.")]
    assert leftovers == []


# --- load_exact_packet edge cases -----------------------------------------


def test_load_exact_packet_malformed_json(tmp_path):
    (tmp_path / "r1.json").write_text("{not json")
    with pytest.raises(ValueError, match="malformed JSON"):
        load_exact_packet(tmp_path, "r1")


def test_load_exact_packet_empty_file(tmp_path):
    (tmp_path / "r1.json").write_text("")
    with pytest.raises(ValueError, match="empty"):
        load_exact_packet(tmp_path, "r1")


def test_load_exact_packet_non_object(tmp_path):
    (tmp_path / "r1.json").write_text("[1, 2, 3]")
    with pytest.raises(ValueError, match="not a JSON object"):
        load_exact_packet(tmp_path, "r1")


def test_load_exact_packet_success(tmp_path):
    (tmp_path / "r1.json").write_text(json.dumps({"a": 1}))
    loaded = load_exact_packet(tmp_path, "r1")
    assert loaded == {"a": 1}


def test_load_exact_packet_never_picks_stale_by_mtime(tmp_path):
    import os
    import time as _time

    stale = tmp_path / "stale.json"
    stale.write_text("{}")
    old = _time.time() - 100000
    os.utime(stale, (old, old))
    fresh = tmp_path / "r1.json"
    fresh.write_text(json.dumps({"fresh": True}))
    # Even though stale.json has an older mtime, load_exact_packet must return r1.json.
    loaded = load_exact_packet(tmp_path, "r1")
    assert loaded == {"fresh": True}


# --- sha256_file -----------------------------------------------------------


def test_sha256_file_streaming(tmp_path):
    path = tmp_path / "data.bin"
    payload = b"abc" * (1024 * 1024)
    path.write_bytes(payload)
    import hashlib
    expected = hashlib.sha256(payload).hexdigest()
    assert sha256_file(path) == expected


# --- LeanExecutionResult immutability --------------------------------------


def test_lean_execution_result_is_frozen():
    result = LeanExecutionResult(run_id="r", status=SUCCEEDED, exit_code=0)
    with pytest.raises(FrozenInstanceError):
        result.run_id = "x"


def test_lean_execution_result_rejects_unknown_status():
    with pytest.raises(ValueError, match="unknown LeanExecutionResult status"):
        LeanExecutionResult(run_id="r", status="NOPE", exit_code=0)


def test_lean_execution_result_is_success():
    ok = LeanExecutionResult(run_id="r", status=SUCCEEDED, exit_code=0)
    assert ok.is_success()
    bad = LeanExecutionResult(run_id="r", status=FAILED_NONZERO_EXIT, exit_code=1)
    assert not bad.is_success()


# --- classify_outcome ------------------------------------------------------


def test_classify_timeout():
    assert (
        classify_outcome(
            timed_out=True,
            exit_code=None,
            packet_exists=False,
            trace_exists=False,
            trace_nonempty=False,
            trace_path_given=True,
        )
        == FAILED_TIMEOUT
    )


def test_classify_missing_packet_zero_exit():
    assert (
        classify_outcome(
            timed_out=False,
            exit_code=0,
            packet_exists=False,
            trace_exists=False,
            trace_nonempty=False,
            trace_path_given=True,
        )
        == FAILED_MISSING_PACKET
    )


def test_classify_missing_packet_nonzero_exit():
    assert (
        classify_outcome(
            timed_out=False,
            exit_code=1,
            packet_exists=False,
            trace_exists=False,
            trace_nonempty=False,
            trace_path_given=True,
        )
        == FAILED_MISSING_PACKET
    )


def test_classify_evidence_capture_trace_missing():
    assert (
        classify_outcome(
            timed_out=False,
            exit_code=1,
            packet_exists=True,
            trace_exists=False,
            trace_nonempty=False,
            trace_path_given=True,
        )
        == FAILED_EVIDENCE_CAPTURE
    )


def test_classify_evidence_capture_trace_empty():
    assert (
        classify_outcome(
            timed_out=False,
            exit_code=1,
            packet_exists=True,
            trace_exists=True,
            trace_nonempty=False,
            trace_path_given=True,
        )
        == FAILED_EVIDENCE_CAPTURE
    )


def test_classify_nonzero_exit_trace_complete():
    assert (
        classify_outcome(
            timed_out=False,
            exit_code=1,
            packet_exists=True,
            trace_exists=True,
            trace_nonempty=True,
            trace_path_given=True,
        )
        == FAILED_NONZERO_EXIT
    )


def test_classify_nonzero_exit_no_trace_configured():
    assert (
        classify_outcome(
            timed_out=False,
            exit_code=1,
            packet_exists=True,
            trace_exists=False,
            trace_nonempty=False,
            trace_path_given=False,
        )
        == FAILED_NONZERO_EXIT
    )


def test_classify_succeeded():
    assert (
        classify_outcome(
            timed_out=False,
            exit_code=0,
            packet_exists=True,
            trace_exists=True,
            trace_nonempty=True,
            trace_path_given=True,
        )
        == SUCCEEDED
    )


# --- run_lean with fakes ---------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_fake_run(packet_written=True, trace_written=True, returncode=0):
    def fake_run(cmd, *args, **kwargs):
        # cmd = [dotnet, launcher_dll, "--config", config_path]
        config_path = Path(cmd[3])
        run_dir = config_path.parent
        # Read config to find packet name and trace path.
        config = json.loads(config_path.read_text())
        run_id = config["algorithm-id"]
        if packet_written:
            (run_dir / f"{run_id}.json").write_text(
                json.dumps({"results": {"TotalPerformance": {}}})
            )
        if trace_written:
            trace = config.get("parameters", {}).get("formal-trace-path")
            if trace:
                Path(trace).parent.mkdir(parents=True, exist_ok=True)
                Path(trace).write_text('{"event": "DECISION"}\n')
        return _FakeCompleted(returncode=returncode, stdout="ok", stderr="")
    return fake_run


def test_run_lean_succeeded(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    trace = tmp_path / "trace.jsonl"
    config = build_run_config(
        BASE, run_dir, "Gold2ClosedLoopProofStrategy", {}, run_id="r1", trace_path=trace
    )
    monkeypatch.setattr(subprocess, "run", _make_fake_run())
    result = run_lean(
        config,
        run_dir=run_dir,
        run_id="r1",
        timeout_seconds=60,
        worktree_root=tmp_path,
        trace_path=trace,
    )
    assert result.status == SUCCEEDED
    assert result.exit_code == 0
    assert result.packet_path == str((run_dir / "r1.json").resolve())
    assert result.packet_sha256 is not None
    assert result.trace_sha256 is not None
    assert result.error is None


def test_run_lean_failed_nonzero_exit_trace_complete(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    trace = tmp_path / "trace.jsonl"
    config = build_run_config(
        BASE, run_dir, "Gold2ClosedLoopProofStrategy", {}, run_id="r1", trace_path=trace
    )
    monkeypatch.setattr(subprocess, "run", _make_fake_run(returncode=2))
    result = run_lean(
        config,
        run_dir=run_dir,
        run_id="r1",
        timeout_seconds=60,
        worktree_root=tmp_path,
        trace_path=trace,
    )
    assert result.status == FAILED_NONZERO_EXIT
    assert result.exit_code == 2
    assert result.packet_sha256 is not None


def test_run_lean_failed_evidence_capture_trace_missing(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    trace = tmp_path / "trace.jsonl"
    config = build_run_config(
        BASE, run_dir, "Gold2ClosedLoopProofStrategy", {}, run_id="r1", trace_path=trace
    )
    monkeypatch.setattr(subprocess, "run", _make_fake_run(packet_written=True, trace_written=False, returncode=1))
    result = run_lean(
        config,
        run_dir=run_dir,
        run_id="r1",
        timeout_seconds=60,
        worktree_root=tmp_path,
        trace_path=trace,
    )
    assert result.status == FAILED_EVIDENCE_CAPTURE
    assert result.trace_sha256 is None


def test_run_lean_failed_missing_packet(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    trace = tmp_path / "trace.jsonl"
    config = build_run_config(
        BASE, run_dir, "Gold2ClosedLoopProofStrategy", {}, run_id="r1", trace_path=trace
    )
    monkeypatch.setattr(subprocess, "run", _make_fake_run(packet_written=False, trace_written=True, returncode=1))
    result = run_lean(
        config,
        run_dir=run_dir,
        run_id="r1",
        timeout_seconds=60,
        worktree_root=tmp_path,
        trace_path=trace,
    )
    assert result.status == FAILED_MISSING_PACKET
    assert result.packet_sha256 is None
    assert "expected result packet" in (result.error or "")


def test_run_lean_timeout(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    trace = tmp_path / "trace.jsonl"
    config = build_run_config(
        BASE, run_dir, "Gold2ClosedLoopProofStrategy", {}, run_id="r1", trace_path=trace
    )

    def fake_timeout(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=60)

    monkeypatch.setattr(subprocess, "run", fake_timeout)
    result = run_lean(
        config,
        run_dir=run_dir,
        run_id="r1",
        timeout_seconds=60,
        worktree_root=tmp_path,
        trace_path=trace,
    )
    assert result.status == FAILED_TIMEOUT
    assert result.exit_code is None
    assert "timed out" in (result.error or "").lower()


def test_run_lean_never_globs_for_packet(tmp_path, monkeypatch):
    """A stale packet with a different run_id must never be substituted."""
    run_dir = tmp_path / "run"
    # Pre-existing stale packet from a different run.
    (run_dir).mkdir(parents=True, exist_ok=True)
    (run_dir / "stale.json").write_text(json.dumps({"stale": True}))
    trace = tmp_path / "trace.jsonl"
    config = build_run_config(
        BASE, run_dir, "Gold2ClosedLoopProofStrategy", {}, run_id="fresh", trace_path=trace
    )
    monkeypatch.setattr(subprocess, "run", _make_fake_run(packet_written=False, trace_written=True, returncode=0))
    result = run_lean(
        config,
        run_dir=run_dir,
        run_id="fresh",
        timeout_seconds=60,
        worktree_root=tmp_path,
        trace_path=trace,
    )
    # No fresh.json exists -> FAILED_MISSING_PACKET, never silently reads stale.json.
    assert result.status == FAILED_MISSING_PACKET


def test_run_lean_config_path_absolute(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    trace = tmp_path / "trace.jsonl"
    config = build_run_config(
        BASE, run_dir, "Gold2ClosedLoopProofStrategy", {}, run_id="r1", trace_path=trace
    )
    monkeypatch.setattr(subprocess, "run", _make_fake_run())
    result = run_lean(
        config,
        run_dir=run_dir,
        run_id="r1",
        timeout_seconds=60,
        worktree_root=tmp_path,
        trace_path=trace,
    )
    assert Path(result.config_path).is_absolute()
    assert Path(result.config_path).is_file()
