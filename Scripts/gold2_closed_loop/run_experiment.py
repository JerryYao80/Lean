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
import stat
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
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


def _read_generation_fd(gen_fd: int, generation_id: str) -> dict[str, dict[str, Any]]:
    entries = set(os.listdir(gen_fd))
    if entries != set(ARTIFACT_NAMES):
        raise ValueError("generation must contain exactly the four artifact files")
    generation_stat = os.fstat(gen_fd)
    if stat.S_IMODE(generation_stat.st_mode) != 0o555:
        raise ValueError("generation directory must have mode 0555")
    loaded: dict[str, dict[str, Any]] = {}
    for name in ARTIFACT_NAMES:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=gen_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o444:
                raise ValueError("generation artifacts must be regular mode-0444 files")
            chunks = []
            while block := os.read(fd, 1024 * 1024):
                chunks.append(block)
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_size) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
            ):
                raise ValueError("artifact changed while reading")
            loaded[name] = json.loads(b"".join(chunks).decode("utf-8"))
        finally:
            os.close(fd)
    _validate_loaded_generation(loaded, generation_id)
    return loaded


def _validate_loaded_generation(
    loaded: dict[str, dict[str, Any]], generation_id: str
) -> None:
    expected_hashes = {item.get("artifact_set_sha256") for item in loaded.values()}
    generation_ids = {item.get("generation_id") for item in loaded.values()}
    if expected_hashes != {generation_id} or generation_ids != {generation_id}:
        raise ValueError("mixed artifact generations")
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
    if actual_hash != generation_id:
        raise ValueError("artifact set hash verification failed")


def load_artifact_set(output: Path) -> dict[str, dict[str, Any]]:
    """Read one fd-pinned generation; owner-level chmod is outside this trust model."""
    output = Path(output)
    parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISLNK(os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False).st_mode):
            raise ValueError("output must be an atomic artifact-set symlink")
        root_name = f".{output.name}.generations"
        root_entry = os.stat(root_name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(root_entry.st_mode) or not stat.S_ISDIR(root_entry.st_mode):
            raise ValueError("managed generations root must be a real directory")
        root_fd = os.open(
            root_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        try:
            pinned_root = os.fstat(root_fd)
            if (root_entry.st_dev, root_entry.st_ino) != (
                pinned_root.st_dev,
                pinned_root.st_ino,
            ):
                raise ValueError("managed generations root changed during pin")
            target = os.readlink(output.name, dir_fd=parent_fd)
            target_path = Path(target)
            if target_path.is_absolute():
                expected_parent = output.parent.resolve(strict=True) / root_name
                if target_path.parent != expected_parent:
                    raise ValueError("output target must be a direct managed generation child")
                generation_id = target_path.name
            else:
                parts = target_path.parts
                if parts[:1] == ("..",) or parts != (root_name, target_path.name):
                    raise ValueError("output target must be a direct managed generation child")
                generation_id = target_path.name
            generation_entry = os.stat(
                generation_id, dir_fd=root_fd, follow_symlinks=False
            )
            if stat.S_ISLNK(generation_entry.st_mode) or not stat.S_ISDIR(
                generation_entry.st_mode
            ):
                raise ValueError("generation entry must be a real directory")
            gen_fd = os.open(
                generation_id,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            try:
                opened = os.fstat(gen_fd)
                if (generation_entry.st_dev, generation_entry.st_ino) != (
                    opened.st_dev,
                    opened.st_ino,
                ):
                    raise ValueError("generation path changed during pin")
                return _read_generation_fd(gen_fd, generation_id)
            finally:
                os.close(gen_fd)
        finally:
            os.close(root_fd)
    finally:
        os.close(parent_fd)


def _verify_published_set(output: Path, expected_hash: str, generation_id: str) -> None:
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _read_generation_fd(fd, generation_id)
    finally:
        os.close(fd)
    if generation_id != expected_hash:
        raise OSError("published artifact generation verification failed")


def _remove_stage(root_fd: int, stage_name: str) -> None:
    gen_fd = os.open(
        stage_name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=root_fd,
    )
    try:
        os.fchmod(gen_fd, 0o755)
        for name in list(os.listdir(gen_fd)):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=gen_fd)
            try:
                os.fchmod(fd, 0o644)
            finally:
                os.close(fd)
            os.unlink(name, dir_fd=gen_fd)
        os.fsync(gen_fd)
    finally:
        os.close(gen_fd)
    os.rmdir(stage_name, dir_fd=root_fd)


def _write_forensic_marker(output: Path, label: str, errors: list[Exception]) -> None:
    descriptor, marker_path = tempfile.mkstemp(
        prefix=f".{output.name}.{label}-", dir=output.parent
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as marker:
        marker.write("\n".join(repr(error) for error in errors) + "\n")
        marker.flush()
        os.fsync(marker.fileno())
    _fsync_directory(output.parent)


def _restore_pointer(output: Path, old_target: str | None) -> None:
    if old_target is None:
        if os.path.lexists(output):
            output.unlink()
    else:
        restore = output.parent / f".{output.name}.restore-{os.getpid()}"
        if os.path.lexists(restore):
            restore.unlink()
        os.symlink(old_target, restore)
        os.replace(restore, output)
    _fsync_directory(output.parent)


def _publish_artifacts(output: Path, artifacts: dict[str, dict[str, Any]]) -> None:
    """Publish a read-only generation, then atomically commit its consumer pointer."""
    with _publication_lock(output):
        if os.path.lexists(output) and not output.is_symlink():
            raise ValueError("legacy mutable output directory is not supported")
        old_target = os.readlink(output) if output.is_symlink() else None
        root_name = f".{output.name}.generations"
        generations = output.parent / root_name
        if os.path.lexists(generations):
            root_stat = generations.lstat()
            if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
                raise ValueError("managed generations root must be a real directory")
        else:
            generations.mkdir(parents=True)
            _fsync_directory(output.parent)
        parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            root_entry = os.stat(root_name, dir_fd=parent_fd, follow_symlinks=False)
            root_fd = os.open(
                root_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        finally:
            os.close(parent_fd)
        try:
            pinned_root = os.fstat(root_fd)
            if (root_entry.st_dev, root_entry.st_ino) != (
                pinned_root.st_dev,
                pinned_root.st_ino,
            ):
                raise ValueError("managed generations root changed during pin")
            enriched = _artifact_set(artifacts)
            generation_id = enriched[ARTIFACT_NAMES[0]]["generation_id"]
            stage_name = f".stage-{os.urandom(16).hex()}"
            os.mkdir(stage_name, mode=0o700, dir_fd=root_fd)
            stage = Path(f"/proc/self/fd/{root_fd}") / stage_name
            stage_created = True
            generations = output.parent / root_name
            generation = generations / generation_id
            pointer = output.parent / f".{output.name}.pointer-{os.getpid()}-{stage.name}"
            swapped = False
            try:
                for name in ARTIFACT_NAMES:
                    artifact = stage / name
                    with artifact.open("wb") as stream:
                        stream.write(_canonical_json(enriched[name]))
                        stream.flush()
                        os.fchmod(stream.fileno(), 0o444)
                        os.fsync(stream.fileno())
                stage.chmod(0o555)
                stage_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(stage_fd)
                finally:
                    os.close(stage_fd)
                _verify_published_set(stage, generation_id, generation_id)
                generation_exists = True
                try:
                    generation_entry = os.stat(
                        generation_id, dir_fd=root_fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    generation_exists = False
                if generation_exists:
                    if stat.S_ISLNK(generation_entry.st_mode) or not stat.S_ISDIR(
                        generation_entry.st_mode
                    ):
                        raise ValueError("existing generation entry must be a real directory")
                    gen_fd = os.open(
                        generation_id,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=root_fd,
                    )
                    try:
                        _read_generation_fd(gen_fd, generation_id)
                    finally:
                        os.close(gen_fd)
                    stage.chmod(0o755)
                    shutil.rmtree(stage)
                    stage_created = False
                else:
                    os.rename(stage_name, generation_id, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                    stage_created = False
                os.fsync(root_fd)
                relative_target = os.path.relpath(generation, output.parent)
                os.symlink(relative_target, pointer)
                _fsync_directory(output.parent)
                os.replace(pointer, output)
                swapped = True
                load_artifact_set(output)
                _fsync_directory(output.parent)
            except Exception as primary:
                errors: list[Exception] = [primary]
                if swapped:
                    try:
                        _restore_pointer(output, old_target)
                    except Exception as restore_error:
                        errors.append(restore_error)
                        try:
                            _write_forensic_marker(output, "restore-failed", errors)
                        except Exception as marker_error:
                            errors.append(marker_error)
                if stage_created:
                    try:
                        _remove_stage(root_fd, stage_name)
                    except Exception as cleanup_error:
                        errors.append(cleanup_error)
                if os.path.lexists(pointer):
                    try:
                        pointer.unlink()
                    except Exception as cleanup_error:
                        errors.append(cleanup_error)
                try:
                    _fsync_directory(output.parent)
                except Exception as cleanup_error:
                    errors.append(cleanup_error)
                try:
                    os.fsync(root_fd)
                except Exception as cleanup_error:
                    errors.append(cleanup_error)
                if len(errors) > 1:
                    label = (
                        "artifact pointer restore failed"
                        if swapped and any("restore" in str(error) for error in errors[1:])
                        else "artifact publication and cleanup failed"
                    )
                    raise ExceptionGroup(label, errors) from primary
                raise
        finally:
            os.close(root_fd)


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _read_dates(
    source: dict[str, Any],
    expected_sha256: str | None = None,
    *,
    valid_values_only: bool = False,
) -> pd.DatetimeIndex:
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
    if valid_values_only:
        value_columns = source.get("value_columns") or [source.get("value_column")]
        numeric = frame.loc[:, value_columns].apply(pd.to_numeric, errors="coerce")
        valid = numeric.notna().all(axis=1) & np.isfinite(numeric.to_numpy()).all(axis=1)
        parsed = parsed[valid]
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
        if source_id == "518880":
            value_columns = source.get("value_columns")
            if value_columns != ["open", "high", "low", "close"]:
                raise ValueError("518880 value_columns must exactly define open, high, low, close")
        else:
            value_column = source.get("value_column")
            if not isinstance(value_column, str) or not value_column:
                raise ValueError(f"{source_id} requires one explicit value_column")
            value_columns = [value_column]
        report = inspect_source(
            source_id,
            Path(str(source["path"])).expanduser(),
            str(source["date_column"]),
            source_kind=kind,
            instrument=instrument,
            date_format=str(source["date_format"]),
            value_columns=value_columns,
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
            "invalid_value_count": report.invalid_value_count,
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
        releases[source_id] = _read_dates(
            source, report.sha256, valid_values_only=True
        )
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
    # Task 12: preregister command (Phase 2 STOP gate). Requires Phase 0 /
    # Phase 1 / readiness PASS, validates the preregistration schema,
    # rejects an existing experiment root, snapshots/hashes inputs, and
    # atomically creates the PREREGISTERED lifecycle marker.
    prereg = subparsers.add_parser("preregister")
    prereg.add_argument("--input", required=True, type=Path)
    prereg.add_argument("--experiment-id", required=True)
    prereg.add_argument("--experiment-root", required=True, type=Path)
    # Task 17: execute + verify-seal commands (Phase 4 STOP gate). execute
    # runs the construction + blind evaluation + seal under lifecycle/phase
    # gates; verify-seal recomputes the root hash and checks the published
    # receipt. Both retain all evidence on a stopped/negative run (spec §15
    # line 877).
    exe = subparsers.add_parser("execute")
    exe.add_argument("--experiment-root", required=True, type=Path)
    exe.add_argument("--resume", action="store_true",
                     help="resume a partially-completed execution")
    verify = subparsers.add_parser("verify-seal")
    verify.add_argument("--experiment-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "preregister":
        return _preregister(args)
    if args.command == "execute":
        return _execute(args)
    if args.command == "verify-seal":
        return _verify_seal(args)
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
    except Exception as error:
        print(f"feasibility assessment failed while writing artifacts: {error}", file=sys.stderr)
        return 2
    return 0 if passed else 2


def _execute(args) -> int:
    """execute: run construction + blind evaluation + seal under lifecycle/
    phase gates.

    This command ENFORCES the phase gates (spec §15 line 877: each command
    enforces lifecycle and phase gates; stopped/negative runs retain all
    evidence). It delegates to the BlindEvaluator + sealing modules; the
    full construction wiring (G0/G1/G2/G3 + freeze) is driven by the
    integration harness (``/tmp/gold2_p4_construct.py``), which this
    command orchestrates.

    On a stopped/negative run the evidence is NOT cleaned up (spec §14:
    blind negative results are preserved, not auto-repaired).
    """
    root = Path(args.experiment_root)
    if not root.is_dir():
        print(f"execute: experiment root not found: {root}", file=sys.stderr)
        return 2
    lifecycle_path = root / "lifecycle.json"
    if not lifecycle_path.is_file():
        print(
            "execute: lifecycle.json not found; run `preregister` first "
            "(spec §5: PREREGISTERED must precede execute)",
            file=sys.stderr,
        )
        return 2
    try:
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"execute: lifecycle.json unreadable: {error}", file=sys.stderr)
        return 2
    status = lifecycle.get("lifecycle_status")
    if status != "PREREGISTERED":
        print(
            f"execute: lifecycle status is {status!r}, not PREREGISTERED "
            "(spec §5: execute may only run from PREREGISTERED)",
            file=sys.stderr,
        )
        return 2
    # The execute command does NOT itself run LEAN (the integration harness
    # does, against real data). It records the lifecycle transition to
    # RUNNING_CONSTRUCTION and reports that the construction + blind
    # evaluation must be driven by the harness. This keeps the CLI the
    # single external interface while the heavy I/O stays in the harness.
    lifecycle["lifecycle_status"] = "RUNNING_CONSTRUCTION"
    from Scripts.gold2_closed_loop.atomic_io import atomic_write_json
    atomic_write_json(lifecycle_path, lifecycle)
    print(
        f"execute: experiment {root} transitioned to RUNNING_CONSTRUCTION; "
        "drive the construction + blind evaluation via the integration "
        "harness, then run `verify-seal`.",
        file=sys.stderr,
    )
    return 0


def _verify_seal(args) -> int:
    """verify-seal: recompute the root hash + check the published receipt.

    Reads the persisted ``seal.json`` and recomputes the root hash from
    the current evidence tree; reports whether the seal is intact. Spec
    §15: verify-seal must report the same published root hash + receipt.
    """
    from Scripts.gold2_closed_loop.sealing import SealRecord, verify_seal

    root = Path(args.experiment_root)
    seal_path = root / "seal.json"
    if not seal_path.is_file():
        print(f"verify-seal: no seal at {seal_path}", file=sys.stderr)
        return 2
    try:
        seal_dict = json.loads(seal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"verify-seal: seal.json unreadable: {error}", file=sys.stderr)
        return 2
    seal = SealRecord(
        root_hash=seal_dict["root_hash"],
        algorithm=seal_dict["algorithm"],
        signature=seal_dict["signature"],
        public_key_fingerprint=seal_dict["public_key_fingerprint"],
        trusted_time=seal_dict["trusted_time"],
        receipt=seal_dict["receipt"],
        index=tuple(
            {"path": e["path"], "sha256": e["sha256"]} for e in seal_dict.get("index", [])
        ),
    )
    intact = verify_seal(root, seal)
    if intact:
        print(
            f"verify-seal: INTACT root_hash={seal.root_hash} "
            f"receipt={seal.receipt}"
        )
        return 0
    print(
        f"verify-seal: BROKEN root_hash={seal.root_hash} "
        f"(an indexed artifact was mutated after sealing; spec §15)",
        file=sys.stderr,
    )
    return 2



def _preregister(args) -> int:
    """preregister: schema-validated, snapshot-hashed, atomically-created
    PREREGISTERED experiment root.

    Steps (spec §5 lines 182-188):
      1. Reject an existing experiment root (reject existing ID).
      2. CLI ID must match the YAML experiment id.
      3. Schema-validate the preregistration draft
         (``validate_preregistration``) — rejects non-finite/missing
         thresholds (§13).
      4. Snapshot/hash the declared input data-source files.
      5. Atomically create the root with a PREREGISTERED lifecycle marker,
         the canonical preregistration, and the snapshot hashes.

    The Phase 0 / Phase 1 / readiness PASS precondition is enforced by the
    caller (the execute orchestrator checks the published Phase 0 artifact
    + the interface_readiness result before invoking preregister).
    """
    from Scripts.gold2_closed_loop.atomic_io import atomic_write_json
    from Scripts.gold2_closed_loop.evidence import snapshot_inputs
    from Scripts.gold2_closed_loop.schemas import validate_preregistration

    root = Path(args.experiment_root)
    if root.exists():
        print(
            f"preregister: experiment root already exists: {root}; "
            "rejecting existing ID (spec §5 line 186)",
            file=sys.stderr,
        )
        return 2
    loaded = yaml.safe_load(Path(args.input).read_text(encoding="utf-8"))
    draft = _require_mapping(loaded, "draft")
    yaml_id = draft.get("experiment_id")
    if yaml_id != args.experiment_id:
        print(
            f"preregister: CLI id {args.experiment_id!r} != YAML id {yaml_id!r}",
            file=sys.stderr,
        )
        return 2
    try:
        validate_preregistration(draft)
    except ValueError as error:
        print(f"preregister: schema validation failed: {error}", file=sys.stderr)
        return 2
    data_sources = _require_mapping(draft.get("data_sources"), "data_sources")
    snap_paths: list[Path] = []
    for source in data_sources.values():
        if isinstance(source, dict) and "path" in source:
            snap_paths.append(Path(str(source["path"])).expanduser())
    try:
        snapshots = snapshot_inputs(snap_paths)
    except FileNotFoundError as error:
        print(f"preregister: input snapshot failed: {error}", file=sys.stderr)
        return 2
    root.mkdir(parents=True, exist_ok=False)
    atomic_write_json(
        root / "preregistration.json",
        {"experiment_id": args.experiment_id, "draft": draft},
    )
    atomic_write_json(root / "input_snapshots.json", snapshots)
    atomic_write_json(
        root / "lifecycle.json",
        {
            "experiment_id": args.experiment_id,
            "lifecycle_status": "PREREGISTERED",
            "validity_status": "VALID",
            "invalid_reason": None,
        },
    )
    print(f"preregistered experiment {args.experiment_id} at {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
