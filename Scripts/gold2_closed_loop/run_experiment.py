#!/usr/bin/env python3
"""Read-only Phase 0 feasibility assessment for the Gold2 proof experiment."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, time
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Scripts.gold2_closed_loop.data_sources import SourceKind, inspect_source
from Scripts.gold2_closed_loop.feasibility import (
    REQUIRED_SOURCES,
    SourcePolicy,
    decide_window_gate,
    evaluate_fixed_windows,
)
from Scripts.gold2_closed_loop.phase0_types import FeasibilityVerdict, proof_windows


ARTIFACT_NAMES = (
    "data_coverage_audit.json",
    "window_inventory.json",
    "g3_observability.json",
    "interface_readiness.json",
)
GENERATION_RECORD_FIELDS = (
    "generation_id",
    "generation_index",
    "parent_generation_id",
    "generation_cutoff",
    "input_evidence_sha256",
    "candidate_set_sha256",
    "attribution_gap",
    "shaping_weight",
    "pending_candidate_count",
    "convergence_status",
    "trigger_observation_valid",
    "terminal_reason",
)
CANONICAL_REVIEW_INPUT = "frozen_formal_review_bundle"
CANONICAL_FAILURE_BUDGET_POLICY = (
    "failed_generation_consumes_budget_and_breaks_adjacency"
)
KNOWN_INTERFACE_BLOCKERS = (
    "production optimizer hard-codes OptionVolArb CQL trace and policy paths",
    "production optimizer hard-codes ONNX observation dimension 8",
    "Gold2 production manifest observation dimensions and completeness are inconsistent",
    "production generation state is not append-only collision-rejecting persistence",
    "in-memory inspiration trigger data is not guaranteed to reach hypothesis construction",
)


def _blocked_artifacts(message: str) -> dict[str, dict[str, Any]]:
    windows = [
        {
            "eligible": False,
            "evaluated_range": [window.train[0].isoformat(), window.blind[1].isoformat()],
            "reasons": ["INSUFFICIENT_DATA"],
            "window_id": window.window_id,
        }
        for window in proof_windows()
    ]
    return {
        "data_coverage_audit.json": {
            "errors": [message],
            "sources": {},
            "status": "BLOCKED",
        },
        "window_inventory.json": {
            "eligible_blind_window_count": 0,
            "errors": [message],
            "verdict": FeasibilityVerdict.BLOCKED_INSUFFICIENT_DATA.value,
            "evaluation_status": "NOT_EVALUATED",
            "proof_conclusion": "UNPROVEN",
            "overall_status": "BLOCKED",
            "windows": windows,
        },
        "g3_observability.json": {
            "errors": [message],
            "required_generation_record_fields": list(GENERATION_RECORD_FIELDS),
            "status": "BLOCKED",
        },
        "interface_readiness.json": _interface_artifact(),
    }


def _interface_artifact() -> dict[str, Any]:
    return {
        "blockers": [
            {"description": description, "status": "NOT_ASSESSED"}
            for description in KNOWN_INTERFACE_BLOCKERS
        ],
        "phase0_gate_effect": "INFORMATIONAL_ONLY",
        "status": "NOT_ASSESSED",
    }


def _unexpected_output_entries(output: Path) -> list[str]:
    if not output.exists():
        return []
    return sorted(path.name for path in output.iterdir() if path.name not in ARTIFACT_NAMES)


def _validate_output(output: Path) -> None:
    if output.exists() and not output.is_dir():
        raise ValueError("output must be a directory")
    unexpected = _unexpected_output_entries(output)
    if unexpected:
        raise ValueError(f"output contains unexpected files: {', '.join(unexpected)}")


@contextmanager
def _publication_lock(output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / f".{output.name}.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _artifact_set(artifacts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    bare = {
        name: {
            key: value
            for key, value in artifacts[name].items()
            if key not in ("generation_id", "artifact_set_sha256")
        }
        for name in ARTIFACT_NAMES
    }
    digest = hashlib.sha256(
        json.dumps(bare, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    generation_id = digest
    return {
        name: {
            **bare[name],
            "artifact_set_sha256": digest,
            "generation_id": generation_id,
        }
        for name in ARTIFACT_NAMES
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_published_set(output: Path, expected_hash: str, generation_id: str) -> None:
    loaded = {
        name: json.loads((output / name).read_text(encoding="utf-8"))
        for name in ARTIFACT_NAMES
    }
    bare = {
        name: {
            key: value
            for key, value in loaded[name].items()
            if key not in ("generation_id", "artifact_set_sha256")
        }
        for name in ARTIFACT_NAMES
    }
    actual_hash = hashlib.sha256(
        json.dumps(bare, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    if actual_hash != expected_hash or any(
        artifact.get("generation_id") != generation_id
        or artifact.get("artifact_set_sha256") != expected_hash
        for artifact in loaded.values()
    ):
        raise OSError("published artifact generation verification failed")


def _write_cleanup_failure_marker(
    parent: Path, output_name: str, errors: list[Exception]
) -> None:
    descriptor, marker_name = tempfile.mkstemp(
        prefix=f".{output_name}.cleanup-failed-", dir=parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as marker:
            marker.write("\n".join(repr(error) for error in errors) + "\n")
            marker.flush()
            os.fsync(marker.fileno())
        _fsync_directory(parent)
    except Exception:
        # The marker path itself remains as best-effort forensic evidence.
        raise


def _cleanup_transaction_dirs(
    stage: Path,
    backup: Path,
    parent: Path,
    *,
    retain_backup: bool,
) -> None:
    """Remove transaction debris durably; failed rollback retains its backup evidence."""
    errors: list[Exception] = []
    for path, retain in ((stage, False), (backup, retain_backup)):
        if not retain:
            try:
                shutil.rmtree(path)
            except Exception as error:
                errors.append(error)
        try:
            _fsync_directory(parent)
        except Exception as error:
            errors.append(error)
    try:
        _fsync_directory(parent)
    except Exception as error:
        errors.append(error)
    if errors:
        output_name = stage.name[1:].split(".stage-", 1)[0]
        try:
            _write_cleanup_failure_marker(parent, output_name, errors)
        except Exception as marker_error:
            errors.append(marker_error)
        raise ExceptionGroup("publication cleanup failed", errors)


def _publish_artifacts(output: Path, artifacts: dict[str, dict[str, Any]]) -> None:
    with _publication_lock(output):
        _validate_output(output)
        unexpected = _unexpected_output_entries(output)
        if unexpected:
            raise ValueError(f"output contains unexpected files: {', '.join(unexpected)}")
        output.mkdir(exist_ok=True)
        enriched = _artifact_set(artifacts)
        generation_hash = enriched[ARTIFACT_NAMES[0]]["artifact_set_sha256"]
        generation_id = enriched[ARTIFACT_NAMES[0]]["generation_id"]
        stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
        backup = Path(tempfile.mkdtemp(prefix=f".{output.name}.backup-", dir=output.parent))
        replaced: list[str] = []
        publication_error: Exception | None = None
        rollback_errors: list[Exception] = []
        try:
            for name in ARTIFACT_NAMES:
                with (stage / name).open("wb") as stream:
                    stream.write(_canonical_json(enriched[name]))
                    stream.flush()
                    os.fsync(stream.fileno())
            _fsync_directory(stage)
            unexpected = _unexpected_output_entries(output)
            if unexpected:
                raise ValueError(f"output contains unexpected files: {', '.join(unexpected)}")
            for name in ARTIFACT_NAMES:
                destination = output / name
                if destination.exists():
                    shutil.copy2(destination, backup / name)
                    with (backup / name).open("rb") as stream:
                        os.fsync(stream.fileno())
            _fsync_directory(backup)
            for name in ARTIFACT_NAMES:
                destination = output / name
                os.replace(stage / name, destination)
                replaced.append(name)
            unexpected = _unexpected_output_entries(output)
            if unexpected:
                raise ValueError(f"output contains unexpected files: {', '.join(unexpected)}")
            _fsync_directory(output)
            _verify_published_set(output, generation_hash, generation_id)
            _fsync_directory(output.parent)
        except Exception as error:
            publication_error = error
            for name in reversed(replaced):
                saved = backup / name
                destination = output / name
                try:
                    if saved.exists():
                        os.replace(saved, destination)
                    elif destination.exists():
                        destination.unlink()
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            for directory in (output, output.parent):
                try:
                    _fsync_directory(directory)
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)

        cleanup_error: Exception | None = None
        try:
            _cleanup_transaction_dirs(
                stage,
                backup,
                output.parent,
                retain_backup=bool(rollback_errors),
            )
        except Exception as error:
            cleanup_error = error

        failure: Exception | None = publication_error
        if rollback_errors:
            failure = ExceptionGroup(
                "publication rollback failed",
                [publication_error, *rollback_errors] if publication_error else rollback_errors,
            )
        if cleanup_error:
            failure = ExceptionGroup(
                "publication and cleanup failed" if failure else "publication cleanup failed",
                [failure, cleanup_error] if failure else [cleanup_error],
            )
        if failure:
            raise failure


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _read_dates(source: dict[str, Any], expected_sha256: str | None = None) -> pd.DatetimeIndex:
    path = Path(str(source["path"])).expanduser()
    content = path.read_bytes()
    if expected_sha256 is not None and hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError(f"source file changed after inspection: {path}")
    suffix = path.suffix.lower()
    from io import BytesIO
    frame = pd.read_parquet(BytesIO(content)) if suffix == ".parquet" else pd.read_csv(BytesIO(content))
    column = str(source["date_column"])
    date_format = str(source["date_format"])
    parsed = pd.to_datetime(frame[column].astype(str), format=date_format, errors="raise")
    timezone = ZoneInfo(str(source["timezone"]))
    observation_time = datetime.strptime(str(source["observation_time"]), "%H:%M:%S").time()
    return pd.DatetimeIndex(
        [datetime.combine(value.date(), observation_time, timezone) for value in parsed]
    ).drop_duplicates().sort_values()


def _read_china_calendar(
    config: dict[str, Any], timezone: ZoneInfo, close_time: time
) -> tuple[pd.DatetimeIndex, dict[str, Any], pd.DatetimeIndex]:
    calendar = _require_mapping(config.get("china_calendar"), "china_calendar")
    required = ("path", "date_column", "date_format", "is_open_column", "source_version")
    missing = [field for field in required if field not in calendar]
    if missing:
        raise ValueError(f"china_calendar missing: {', '.join(missing)}")
    path = Path(str(calendar["path"])).expanduser().resolve(strict=True)
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    suffix = path.suffix.lower()
    if suffix not in (".csv", ".parquet"):
        raise ValueError(f"unsupported China calendar suffix: {suffix}")
    from io import BytesIO
    frame = pd.read_parquet(BytesIO(content)) if suffix == ".parquet" else pd.read_csv(BytesIO(content))
    date_column = str(calendar["date_column"])
    open_column = str(calendar["is_open_column"])
    if date_column not in frame or open_column not in frame:
        raise ValueError("China calendar date or is_open column does not exist")
    parsed = pd.to_datetime(
        frame[date_column].astype(str), format=str(calendar["date_format"]), errors="raise"
    )
    if parsed.duplicated().any():
        raise ValueError("China calendar contains duplicate dates")
    all_dates = pd.DatetimeIndex(parsed).sort_values()
    open_values = pd.to_numeric(frame.set_index(parsed)[open_column], errors="raise")
    if not set(open_values.unique()).issubset({0, 1}):
        raise ValueError("China calendar is_open values must be 0 or 1")
    open_dates = open_values[open_values == 1].index.sort_values()
    sessions = pd.DatetimeIndex(
        [datetime.combine(value.date(), close_time, timezone) for value in open_dates]
    )
    report = {
        "date_column": date_column,
        "date_format": str(calendar["date_format"]),
        "first_date": all_dates.min().date().isoformat(),
        "is_open_column": open_column,
        "last_date": all_dates.max().date().isoformat(),
        "open_session_count": len(sessions),
        "path": str(path),
        "sha256": digest,
        "source_id": "china_calendar",
        "source_version": str(calendar["source_version"]),
    }
    return sessions, report, all_dates


def _duration(value: Any, label: str) -> pd.Timedelta:
    try:
        duration = pd.Timedelta(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a pandas-compatible duration") from error
    if pd.isna(duration) or duration < pd.Timedelta(0):
        raise ValueError(f"{label} must be non-negative")
    return duration


def _assess_data(config: dict[str, Any]) -> tuple[
    dict[str, Any],
    pd.DatetimeIndex,
    pd.DatetimeIndex,
    dict[str, pd.DatetimeIndex],
    dict[str, SourcePolicy],
]:
    declared = _require_mapping(config.get("data_sources"), "data_sources")
    if set(declared) != set(REQUIRED_SOURCES):
        raise ValueError("data_sources must define exactly 518880, AU, VIX, DFII10")

    reports: dict[str, Any] = {}
    releases: dict[str, pd.DatetimeIndex] = {}
    policies: dict[str, SourcePolicy] = {}
    for source_id in REQUIRED_SOURCES:
        source = _require_mapping(declared[source_id], f"data_sources.{source_id}")
        required = (
            "source_id", "path", "date_column", "date_format", "timezone",
            "observation_time", "publication_lag", "maximum_staleness_days",
            "maximum_consecutive_gap_sessions",
            "adjustment_rule", "source_version",
        )
        missing = [field for field in required if field not in source]
        if missing:
            raise ValueError(f"data_sources.{source_id} missing: {', '.join(missing)}")
        declared_id = str(source["source_id"])
        instrument = str(source.get("instrument", ""))
        if declared_id != source_id:
            raise ValueError(f"{source_id} source must use canonical source_id {source_id}")
        if instrument != source_id:
            raise ValueError(f"{source_id} source must use canonical instrument {source_id}")
        kind_text = str(source.get("source_kind", "tradable" if source_id == "518880" else "feature"))
        kind = SourceKind(kind_text)
        if source_id == "518880" and kind is not SourceKind.TRADABLE:
            raise ValueError("518880 must be a tradable source; fund_nav and proxies are forbidden")
        report = inspect_source(
            source_id,
            Path(str(source["path"])).expanduser(),
            str(source["date_column"]),
            source_kind=kind,
            instrument=instrument,
            date_format=str(source["date_format"]),
        )
        report_value = {
            "annual_counts": dict(report.annual_counts),
            "distinct_date_count": report.distinct_date_count,
            "duplicate_date_count": report.duplicate_date_count,
            "first_date": report.first_date,
            "last_date": report.last_date,
            "logical_name": report.logical_name,
            "path": report.path,
            "row_count": report.row_count,
            "sha256": report.sha256,
        }
        report_value["source_id"] = declared_id
        report_value["source_kind"] = kind.value
        for policy_field in (
            "timezone", "observation_time", "publication_lag",
            "maximum_staleness_days", "maximum_consecutive_gap_sessions",
            "adjustment_rule", "source_version", "date_column", "date_format",
        ):
            report_value[policy_field] = source[policy_field]
        reports[source_id] = report_value
        releases[source_id] = _read_dates(source, report.sha256)
        policies[source_id] = SourcePolicy(
            maximum_staleness=source["maximum_staleness_days"],
            maximum_consecutive_unavailable_sessions=source["maximum_consecutive_gap_sessions"],
            publication_lag=_duration(source["publication_lag"], f"{source_id}.publication_lag"),
            timezone=str(source["timezone"]),
        )

    session_timezone = ZoneInfo(str(config.get("session_timezone", "Asia/Shanghai")))
    close_time = datetime.strptime(str(config.get("session_close_time", "15:00:00")), "%H:%M:%S").time()
    sessions, calendar_report, calendar_dates = _read_china_calendar(
        config, session_timezone, close_time
    )
    artifact = {
        "calendar_basis": {
            "description": "explicit China trade calendar open sessions at configured close",
            "observed_first_date": calendar_report["first_date"],
            "observed_last_date": calendar_report["last_date"],
            "session_close_time": close_time.isoformat(),
            "timezone": str(session_timezone),
            "source_id": "china_calendar",
        },
        "china_calendar": calendar_report,
        "sources": reports,
        "status": "PASS",
    }
    return artifact, sessions, calendar_dates, releases, policies


def _assess_windows(
    sessions: pd.DatetimeIndex,
    calendar_dates: pd.DatetimeIndex,
    releases: dict[str, pd.DatetimeIndex],
    policies: dict[str, SourcePolicy],
) -> dict[str, Any]:
    decision = decide_window_gate(evaluate_fixed_windows(sessions, releases, policies))
    windows = []
    for window, definition in zip(decision.windows, proof_windows(), strict=True):
        reasons = list(window.reasons)
        calendar_start = definition.train[0]
        calendar_end = definition.blind[1]
        if calendar_dates.empty or calendar_dates.min().date() > calendar_start:
            reasons.append(f"INCOMPLETE_CALENDAR_START:{calendar_start.isoformat()}")
        if calendar_dates.empty or calendar_dates.max().date() < calendar_end:
            reasons.append(f"INCOMPLETE_CALENDAR_END:{calendar_end.isoformat()}")
        if not calendar_dates.empty:
            expected_dates = pd.date_range(calendar_start, calendar_end, freq="D")
            missing_dates = expected_dates.difference(calendar_dates.tz_localize(None))
            if not missing_dates.empty:
                reasons.append(
                    f"INCOMPLETE_CALENDAR_INTERNAL:{missing_dates[0].date().isoformat()}"
                )
        relevant = sessions[
            (sessions.date >= definition.train[0]) & (sessions.date <= definition.blind[1])
        ]
        if not relevant.empty:
            if relevant[0].year != definition.train[0].year:
                reasons.append(f"INCOMPLETE_FIXED_WINDOW_START:{relevant[0].date().isoformat()}")
            if relevant[-1].year != definition.blind[1].year:
                reasons.append(f"INCOMPLETE_FIXED_WINDOW_END:{relevant[-1].date().isoformat()}")
        windows.append(
            {
                "eligible": window.eligible and not reasons,
                "evaluated_range": [date.isoformat() for date in window.evaluated_range] if window.evaluated_range else None,
                "reasons": reasons,
                "window_id": window.window_id,
            }
        )
    eligible_count = sum(window["eligible"] for window in windows)
    verdict = (
        FeasibilityVerdict.PASS.value
        if eligible_count >= 3
        else FeasibilityVerdict.BLOCKED_INSUFFICIENT_TEST_WINDOWS.value
    )
    return {
        "eligible_blind_window_count": eligible_count,
        "evaluation_status": "READY" if verdict == FeasibilityVerdict.PASS.value else "NOT_EVALUATED",
        "proof_conclusion": "PENDING_PROOF" if verdict == FeasibilityVerdict.PASS.value else "UNPROVEN",
        "verdict": verdict,
        "windows": windows,
    }


def _assess_g3(config: dict[str, Any]) -> dict[str, Any]:
    value = _require_mapping(config.get("g3_observability"), "g3_observability")
    errors = []
    minimum = value.get("minimum_required_generation_count")
    if type(minimum) is not int or minimum != 3:
        errors.append("minimum_required_generation_count must be integer 3")
    for field in ("per_generation_candidate_budget", "per_window_candidate_budget"):
        number = value.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            errors.append(f"{field} must be a positive integer")
    if value.get("review_input") != CANONICAL_REVIEW_INPUT:
        errors.append(f"review_input must equal {CANONICAL_REVIEW_INPUT}")
    if value.get("failure_budget_policy") != CANONICAL_FAILURE_BUDGET_POLICY:
        errors.append(
            f"failure_budget_policy must equal {CANONICAL_FAILURE_BUDGET_POLICY}"
        )
    per_generation = value.get("per_generation_candidate_budget")
    per_window = value.get("per_window_candidate_budget")
    if (
        isinstance(minimum, int) and not isinstance(minimum, bool)
        and isinstance(per_generation, int) and not isinstance(per_generation, bool)
        and isinstance(per_window, int) and not isinstance(per_window, bool)
        and per_window < minimum * per_generation
    ):
        errors.append(
            "per_window_candidate_budget must cover every required generation budget"
        )
    fields = value.get("required_generation_record_fields")
    if fields != list(GENERATION_RECORD_FIELDS):
        errors.append("required_generation_record_fields must exactly match the proof specification")
    return {
        "errors": errors,
        "minimum_required_generation_count": value.get("minimum_required_generation_count"),
        "per_generation_candidate_budget": value.get("per_generation_candidate_budget"),
        "per_window_candidate_budget": value.get("per_window_candidate_budget"),
        "review_input": value.get("review_input"),
        "failure_budget_policy": value.get("failure_budget_policy"),
        "required_generation_record_fields": list(GENERATION_RECORD_FIELDS),
        "status": "PASS" if not errors else "BLOCKED",
    }


def assess(config: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], bool]:
    data, sessions, calendar_dates, releases, policies = _assess_data(config)
    windows = _assess_windows(sessions, calendar_dates, releases, policies)
    g3 = _assess_g3(config)
    passed = windows["verdict"] == FeasibilityVerdict.PASS.value and g3["status"] == "PASS"
    windows["overall_status"] = "PASS" if passed else "BLOCKED"
    windows["evaluation_status"] = "READY" if passed else "NOT_EVALUATED"
    windows["proof_conclusion"] = "PENDING_PROOF" if passed else "UNPROVEN"
    artifacts = {
        "data_coverage_audit.json": data,
        "window_inventory.json": windows,
        "g3_observability.json": g3,
        "interface_readiness.json": _interface_artifact(),
    }
    return artifacts, passed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("assess-feasibility")
    command.add_argument("--input", required=True, type=Path)
    command.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        loaded = yaml.safe_load(args.input.read_text(encoding="utf-8"))
        config = _require_mapping(loaded, "draft")
        artifacts, passed = assess(config)
    except Exception as error:
        artifacts = _blocked_artifacts(str(error))
        passed = False
        print(f"feasibility assessment blocked: {error}", file=sys.stderr)

    try:
        _publish_artifacts(args.output, artifacts)
    except (OSError, ValueError) as error:
        print(f"feasibility assessment failed while writing artifacts: {error}", file=sys.stderr)
        return 2
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
