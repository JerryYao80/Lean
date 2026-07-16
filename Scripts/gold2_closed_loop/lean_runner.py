"""Isolated LEAN backtest runner for the Gold2 closed-loop proof (Task 7).

Builds a deep-copied, side-effect-disabled config, invokes the proof-only
``Gold2ClosedLoopProofStrategy`` via ``dotnet Launcher.dll --config <path>``,
and loads the EXACT result packet by run id (never glob, never mtime).

Verified engine facts (file:line):
- ``--config <path>`` is a supported CLI flag — ``LeanArgumentParser`` defines
  a ``"config"`` ``SingleValue`` option, and ``Config.MergeCommandLineArgumentsWithConfiguration``
  (Configuration/Config.cs:59-63) calls ``SetConfigurationFile`` for that key.
- ``algorithm-id`` is a supported config key — ``Queues/JobQueue.cs:153``:
  ``Config.Get("algorithm-id", AlgorithmTypeName)`` sets ``BacktestId``/``AlgorithmId``.
- ``BacktestingResultHandler.StoreResult`` (Engine/Results/BacktestingResultHandler.cs:314)
  writes the packet as ``$"{AlgorithmId}.json"`` under ``results-destination-folder``.
- ``fallback-download-enabled``/``fallback-gbm-enabled`` are read from the
  algorithm ``parameters`` dict by ``FallbackTushareHistoryProvider.Initialize``
  (Engine/HistoricalData/FallbackTushareHistoryProvider.cs:66-67) and default
  to ``true`` — the proof MUST force them ``false``.
- ``influxdb-enabled`` is read by ``InfluxDbResultExporter`` (Engine/Results/
  InfluxDbResultExporter.cs:108) and defaults to ``false``; the proof still
  forces it ``false`` explicitly.
- ``QCAlgorithm.SetParameters`` (Algorithm/QCAlgorithm.cs:924-927) receives
  ``job.Parameters`` which JobQueue builds from the config ``parameters`` key
  (Queues/JobQueue.cs:134-137). So every parameter the proof strategy reads
  via ``GetParameter`` flows from the config ``parameters`` mapping.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from Scripts.gold2_closed_loop.lean_artifacts import (
    FAILED_MISSING_PACKET,
    FAILED_TIMEOUT,
    SUCCEEDED,
    LeanExecutionResult,
    classify_outcome,
    sha256_file,
)

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DEFAULT_DOTNET_PATH = "/usr/local/dotnet/dotnet"
DEFAULT_LAUNCHER_DLL = (
    Path(__file__).resolve().parents[2]
    / "Launcher"
    / "bin"
    / "Debug"
    / "QuantConnect.Lean.Launcher.dll"
)
DEFAULT_PROOF_DLL = (
    Path(__file__).resolve().parents[2]
    / "Algorithm.CSharp"
    / "bin"
    / "Debug"
    / "QuantConnect.Algorithm.CSharp.dll"
)

# Identity parameter names the proof strategy / TracingExecutionModel reads.
_IDENTITY_PARAMS = (
    "experiment-id",
    "window-id",
    "stage-id",
    "run-id",
    "candidate-id",
)
_TRACE_PARAM = "formal-trace-path"

_STDERR_TAIL_CHARS = 4096


def build_run_config(
    base: dict[str, Any],
    run_dir: Path,
    algorithm_type_name: str,
    parameters: dict[str, Any],
    *,
    run_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    trace_path: Path | None = None,
    experiment_id: str | None = None,
    window_id: str | None = None,
    stage_id: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Deep-copy ``base`` and inject proof-run overrides.

    The caller's ``base`` is never mutated (``copy.deepcopy``). The returned
    config is a plain dict the caller writes to JSON.

    Injected / forced fields
    -----------------------
    environment            : "backtesting"
    algorithm-language     : "CSharp"
    algorithm-type-name    : ``algorithm_type_name``
    algorithm-location     : absolute resolved proof DLL (DEFAULT_PROOF_DLL
                             unless the base already points to a valid
                             QuantConnect.Algorithm.CSharp.dll)
    results-destination-folder : ``str(run_dir.resolve())`` (ABSOLUTE, unique)
    algorithm-id           : ``run_id`` so the packet is ``<run_dir>/<run_id>.json``
                             (LEAN honors this config key — see JobQueue.cs:153
                             and BacktestingResultHandler.cs:314)
    influxdb-enabled         : False (TOP-LEVEL config key; read by
                             InfluxDbResultExporter via Config.Get)
    fallback-download-enabled / fallback-gbm-enabled : "false" injected into
                             ``parameters`` (NOT top-level). Read from
                             parameters.Job.Parameters by
                             FallbackTushareHistoryProvider.cs:66-67, default
                             True — placing them at top-level leaves the
                             fallbacks silently ACTIVE (silent false proof).
                             Any stale top-level copies are removed.
    parameters             : merged base parameters + caller parameters +
                             ``formal-trace-path`` (REQUIRED) + identity params
                             (experiment/window/stage/run/candidate id) +
                             start-date/end-date + forced fallback flags
    """
    config = copy.deepcopy(base)
    config["environment"] = "backtesting"
    config["algorithm-language"] = "CSharp"
    config["algorithm-type-name"] = algorithm_type_name

    # Resolve algorithm-location to an absolute built proof DLL.
    location = config.get("algorithm-location")
    location_path = Path(location) if location else None
    if location_path is not None and location_path.exists():
        config["algorithm-location"] = str(location_path.resolve())
    else:
        config["algorithm-location"] = str(DEFAULT_PROOF_DLL.resolve())

    # Absolute, unique results-destination-folder per run.
    config["results-destination-folder"] = str(Path(run_dir).resolve())

    # Deterministic packet name: <run_dir>/<run_id>.json.
    # When run_id is provided, set algorithm-id so the packet is exactly
    # <run_dir>/<run_id>.json (JobQueue.cs:153, BacktestingResultHandler.cs:314).
    # run_id is optional here so build_run_config can be used for pure config
    # construction in tests; run_lean REQUIRES it to locate the packet.
    if run_id is not None:
        config["algorithm-id"] = run_id

    # Disable every side-effect fallback the proof must not depend on.
    # influxdb-enabled is a TOP-LEVEL config key read by InfluxDbResultExporter
    # (Engine/Results/InfluxDbResultExporter.cs:108 via Config.Get), so it is
    # forced at the top level here.
    config["influxdb-enabled"] = False

    # CRITICAL: fallback-download-enabled / fallback-gbm-enabled are NOT
    # top-level config keys. FallbackTushareHistoryProvider.Initialize
    # (Engine/HistoricalData/FallbackTushareHistoryProvider.cs:66-67) reads
    # them from parameters.Job.Parameters (the algorithm `parameters` dict),
    # defaulting to TRUE when absent. They MUST be injected into the
    # `parameters` dict (as strings, since Job.Parameters is
    # Dictionary<string,string>) or the fallbacks stay silently ACTIVE and a
    # missing symbol triggers a live download / synthetic GBM data — a silent
    # false proof. The stale top-level copies are removed below.
    config.pop("fallback-download-enabled", None)
    config.pop("fallback-gbm-enabled", None)

    # Merge parameters: base -> caller -> required/identity injections.
    merged: dict[str, Any] = {}
    base_params = config.get("parameters")
    if isinstance(base_params, dict):
        merged.update(base_params)
    merged.update(parameters)

    # Force the fallback flags into the parameters dict (string form, as
    # serialized into Dictionary<string,string> by JobQueue.cs:134-137).
    merged["fallback-download-enabled"] = "false"
    merged["fallback-gbm-enabled"] = "false"

    if start_date is not None:
        merged["start-date"] = start_date
    if end_date is not None:
        merged["end-date"] = end_date

    if trace_path is not None:
        merged[_TRACE_PARAM] = str(Path(trace_path).resolve())
    # formal-trace-path is REQUIRED by the proof strategy at runtime; the
    # builder does not enforce it so callers can construct partial configs
    # in tests. run_lean callers MUST pass trace_path or the proof will throw.

    identity = {
        "experiment-id": experiment_id,
        "window-id": window_id,
        "stage-id": stage_id,
        "run-id": run_id,
        "candidate-id": candidate_id,
    }
    for key, value in identity.items():
        if value is not None:
            merged[key] = value
    # run-id defaults to the run_id when provided.
    if run_id is not None:
        merged.setdefault("run-id", run_id)

    config["parameters"] = merged
    return config


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic JSON write: write+fsync tmp, then os.replace (no torn reads)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _tail(text: str, limit: int = _STDERR_TAIL_CHARS) -> str:
    if not text:
        return ""
    return text[-limit:] if len(text) > limit else text


def run_lean(
    config: dict[str, Any],
    *,
    run_dir: Path,
    run_id: str,
    timeout_seconds: int,
    dotnet_path: str = DEFAULT_DOTNET_PATH,
    launcher_dll: Path = DEFAULT_LAUNCHER_DLL,
    worktree_root: Path | None = None,
    trace_path: Path | None = None,
) -> LeanExecutionResult:
    """Invoke LEAN once and return a frozen LeanExecutionResult.

    Stages the config to ``<run_dir>/config.json`` atomically, then runs
    ``[dotnet_path, launcher_dll, "--config", config_path]`` with
    ``subprocess.run(timeout=timeout_seconds, capture_output=True,
    cwd=worktree_root)``. After the process, loads the EXACT
    ``<run_dir>/<run_id>.json`` packet (never glob) and classifies the outcome.

    Never runs more than one LEAN concurrently in the same run_dir — the
    caller owns run_dir uniqueness; the exact packet name makes collisions
    impossible to hide.
    """
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    config_path = run_dir / "config.json"
    _atomic_write_json(config_path, config)

    effective_trace = trace_path
    if effective_trace is None:
        params = config.get("parameters") or {}
        raw_trace = params.get(_TRACE_PARAM)
        if raw_trace:
            effective_trace = Path(raw_trace)

    cmd = [dotnet_path, str(launcher_dll), "--config", str(config_path)]
    cwd = str(worktree_root.resolve()) if worktree_root else None

    start = time.monotonic()
    timed_out = False
    exit_code: int | None
    stdout_text = ""
    stderr_text = ""
    try:
        completed = subprocess.run(
            cmd,
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )
        exit_code = completed.returncode
        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout_text = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr_text = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    elapsed = time.monotonic() - start

    packet_path = run_dir / f"{run_id}.json"
    packet_exists = packet_path.is_file()
    trace_exists = False
    trace_nonempty = False
    trace_path_given = effective_trace is not None
    if effective_trace is not None:
        trace_p = Path(effective_trace)
        trace_exists = trace_p.is_file()
        if trace_exists:
            try:
                trace_nonempty = trace_p.stat().st_size > 0
            except OSError:
                trace_nonempty = False

    status = classify_outcome(
        timed_out=timed_out,
        exit_code=exit_code,
        packet_exists=packet_exists,
        trace_exists=trace_exists,
        trace_nonempty=trace_nonempty,
        trace_path_given=trace_path_given,
    )

    packet_sha: str | None = None
    packet_path_str: str | None = None
    trace_sha: str | None = None
    trace_path_str: str | None = str(effective_trace) if effective_trace else None

    combined = _tail((stdout_text or "") + "\n" + (stderr_text or ""))
    error: str | None = None
    if status == SUCCEEDED:
        error = None
    elif status == FAILED_TIMEOUT:
        error = f"LEAN timed out after {timeout_seconds}s\n{combined}".strip()
    else:
        error = combined or f"LEAN exit {exit_code}"

    if packet_exists:
        try:
            packet_sha = sha256_file(packet_path)
            packet_path_str = str(packet_path)
        except OSError:
            pass

    if trace_exists:
        try:
            trace_sha = sha256_file(effective_trace)
        except OSError:
            pass

    # On missing packet, surface the exact-packet error in the result.
    if status == FAILED_MISSING_PACKET:
        error = (
            f"expected result packet {packet_path} not found in {run_dir}; "
            f"refusing to glob or select by mtime"
        )

    return LeanExecutionResult(
        run_id=run_id,
        status=status,
        exit_code=exit_code,
        config_path=str(config_path),
        packet_path=packet_path_str,
        packet_sha256=packet_sha,
        trace_path=trace_path_str,
        trace_sha256=trace_sha,
        elapsed_seconds=elapsed,
        error=error,
    )
