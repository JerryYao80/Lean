from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pandas as pd
import pytest
import yaml


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "Scripts/gold2_closed_loop/run_experiment.py"
ARTIFACTS = {
    "data_coverage_audit.json",
    "window_inventory.json",
    "g3_observability.json",
    "interface_readiness.json",
}
GENERATION_FIELDS = [
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
]


def run_cli(draft: Path, output: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "assess-feasibility",
            "--input",
            str(draft),
            "--output",
            str(output),
        ],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def write_yaml(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=True), encoding="utf-8")


def source_policy(path: Path, source_id: str, *, fmt: str = "%Y-%m-%d") -> dict:
    return {
        "source_id": source_id,
        "path": str(path),
        "date_column": "date",
        "date_format": fmt,
        "value_column": "value",
        "timezone": "Asia/Shanghai",
        "observation_time": "15:00:00",
        "publication_lag": "0 days",
        "maximum_staleness_days": 5,
        "maximum_consecutive_gap_sessions": 3,
        "adjustment_rule": "none",
        "source_version": "synthetic-v1",
    }


def complete_draft(tmp_path: Path, start: str = "2018-01-02", end: str = "2025-12-31") -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    calendar_days = pd.date_range("2018-01-01", "2025-12-31", freq="D")
    calendar = tmp_path / "china_trade_cal.csv"
    pd.DataFrame(
        {
            "cal_date": calendar_days.strftime("%Y%m%d"),
            "is_open": (calendar_days.dayofweek < 5).astype(int),
        }
    ).to_csv(calendar, index=False)
    dates = pd.bdate_range(start, end)
    tradable = tmp_path / "518880.parquet"
    pd.DataFrame(
        {
            "trade_date": dates.strftime("%Y%m%d").astype(int),
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.0,
        }
    ).to_parquet(tradable, index=False)

    sources = {
        "518880": {
            **source_policy(tradable, "518880", fmt="%Y%m%d"),
            "date_column": "trade_date",
            "value_columns": ["open", "high", "low", "close"],
            "source_kind": "tradable",
            "instrument": "518880",
            "maximum_staleness_days": 0,
            "calendar_basis": True,
        }
    }
    for source_id in ("AU", "VIX", "DFII10"):
        path = tmp_path / f"{source_id}.csv"
        pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "value": 1.0}).to_csv(
            path, index=False
        )
        sources[source_id] = {
            **source_policy(path, source_id),
            "instrument": source_id,
        }

    return {
        "experiment_id": "synthetic-proof",
        "paths": {"experiment_root": "Results/forbidden-proof-root"},
        "session_close_time": "15:00:00",
        "session_timezone": "Asia/Shanghai",
        "china_calendar": {
            "path": str(calendar),
            "date_column": "cal_date",
            "date_format": "%Y%m%d",
            "is_open_column": "is_open",
            "source_version": "synthetic-calendar-v1",
        },
        "data_sources": sources,
        "g3_observability": {
            "minimum_required_generation_count": 3,
            "per_generation_candidate_budget": 2,
            "per_window_candidate_budget": 8,
            "review_input": "frozen_formal_review_bundle",
            "failure_budget_policy": "failed_generation_consumes_budget_and_breaks_adjacency",
            "required_generation_record_fields": GENERATION_FIELDS,
        },
    }


def load(output: Path, name: str) -> dict:
    return json.loads((output / name).read_text(encoding="utf-8"))


def artifact_set(output: Path) -> dict[str, dict]:
    return {name: load(output, name) for name in ARTIFACTS}


def assert_coherent_artifact_set(output: Path) -> None:
    artifacts = artifact_set(output)
    generation_ids = {artifact["generation_id"] for artifact in artifacts.values()}
    set_hashes = {artifact["artifact_set_sha256"] for artifact in artifacts.values()}
    assert len(generation_ids) == len(set_hashes) == 1
    bare = {
        name: {
            key: value
            for key, value in artifacts[name].items()
            if key not in ("generation_id", "artifact_set_sha256")
        }
        for name in sorted(ARTIFACTS)
    }
    expected = hashlib.sha256(
        json.dumps(bare, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert set_hashes == {expected}


def load_cli_module():
    spec = importlib.util.spec_from_file_location("phase0_cli", CLI)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("attack", ("external_absolute", "dotdot", "generation_symlink", "root_symlink", "artifact_symlink"))
def test_loader_rejects_symlink_escape_and_nofollow_attacks(tmp_path: Path, attack: str):
    module = load_cli_module()
    output = tmp_path / "audit"
    module._publish_artifacts(output, {name: {"safe": name} for name in ARTIFACTS})
    root = tmp_path / ".audit.generations"
    generation = output.resolve()
    external = tmp_path / "external"
    if attack in ("external_absolute", "dotdot"):
        external.mkdir()
        for name in ARTIFACTS:
            (external / name).write_bytes((generation / name).read_bytes())
        output.unlink()
        target = str(external) if attack == "external_absolute" else "../external"
        output.symlink_to(target)
    elif attack == "generation_symlink":
        moved = tmp_path / "moved-generation"
        generation.rename(moved)
        generation.symlink_to(moved, target_is_directory=True)
    elif attack == "root_symlink":
        moved = tmp_path / "real-generations"
        root.rename(moved)
        root.symlink_to(moved, target_is_directory=True)
    else:
        artifact = generation / next(iter(ARTIFACTS))
        moved = tmp_path / "artifact.json"
        moved.write_bytes(artifact.read_bytes())
        artifact.unlink()
        artifact.symlink_to(moved)

    with pytest.raises((ValueError, OSError)):
        module.load_artifact_set(output)


def test_published_generation_has_read_only_modes(tmp_path: Path):
    module = load_cli_module()
    output = tmp_path / "audit"
    module._publish_artifacts(output, {name: {"value": name} for name in ARTIFACTS})
    generation = output.resolve()

    assert stat.S_IMODE(generation.stat().st_mode) == 0o555
    assert all(stat.S_IMODE((generation / name).stat().st_mode) == 0o444 for name in ARTIFACTS)
    module.load_artifact_set(output)


def test_loader_pins_generation_when_public_pointer_changes(tmp_path: Path, monkeypatch):
    module = load_cli_module()
    output = tmp_path / "audit"
    old = {name: {"old": name} for name in ARTIFACTS}
    new = {name: {"new": name} for name in ARTIFACTS}
    module._publish_artifacts(output, old)
    old_loaded = module.load_artifact_set(output)
    real_open = module.os.open
    switched = False

    def switch_after_generation_open(path, flags, *args, **kwargs):
        nonlocal switched
        fd = real_open(path, flags, *args, **kwargs)
        if not switched and isinstance(path, str) and len(path) == 64 and kwargs.get("dir_fd") is not None:
            switched = True
            module._publish_artifacts(output, new)
        return fd

    monkeypatch.setattr(module.os, "open", switch_after_generation_open)
    loaded = module.load_artifact_set(output)
    assert loaded == old_loaded


def test_corrupted_existing_hash_generation_is_rejected(tmp_path: Path):
    module = load_cli_module()
    output = tmp_path / "audit"
    artifacts = {name: {"safe": name} for name in ARTIFACTS}
    enriched = module._artifact_set(artifacts)
    generation_id = enriched[next(iter(module.ARTIFACT_NAMES))]["generation_id"]
    generation = tmp_path / ".audit.generations" / generation_id
    generation.mkdir(parents=True)
    for name in ARTIFACTS:
        (generation / name).write_text("{}", encoding="utf-8")

    with pytest.raises((ValueError, OSError)):
        module._publish_artifacts(output, artifacts)
    assert not os.path.lexists(output)


def test_every_artifact_has_matching_generation_and_artifact_set_hash(tmp_path: Path):
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, complete_draft(tmp_path))

    assert run_cli(draft, output, tmp_path).returncode == 0

    assert_coherent_artifact_set(output)
    assert {path.name for path in output.iterdir()} == ARTIFACTS
    assert not list(tmp_path.glob(".audit.stage-*"))
    assert not list(tmp_path.glob(".audit.backup-*"))


def test_post_swap_failure_restores_old_pointer_and_first_publish_removes_output(
    tmp_path: Path, monkeypatch
):
    module = load_cli_module()
    output = tmp_path / "audit"
    module._publish_artifacts(output, {name: {"old": name} for name in ARTIFACTS})
    old_target = os.readlink(output)
    real_load = module.load_artifact_set
    calls = 0

    def fail_post_swap(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected post-swap verification failure")
        return real_load(path)

    monkeypatch.setattr(module, "load_artifact_set", fail_post_swap)
    with pytest.raises(OSError, match="post-swap"):
        module._publish_artifacts(output, {name: {"new": name} for name in ARTIFACTS})
    assert os.readlink(output) == old_target
    real_load(output)

    fresh = tmp_path / "fresh"
    calls = 0
    with pytest.raises(OSError, match="post-swap"):
        module._publish_artifacts(fresh, {name: {"new": name} for name in ARTIFACTS})
    assert not os.path.lexists(fresh)


def test_pointer_restore_failure_is_grouped_and_leaves_forensic_marker(
    tmp_path: Path, monkeypatch
):
    module = load_cli_module()
    output = tmp_path / "audit"
    module._publish_artifacts(output, {name: {"old": name} for name in ARTIFACTS})
    real_replace = module.os.replace
    swap_count = 0

    def fail_restore(source, destination):
        nonlocal swap_count
        if Path(destination) == output:
            swap_count += 1
            if swap_count == 2:
                raise OSError("injected pointer restore failure")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_restore)
    monkeypatch.setattr(
        module,
        "load_artifact_set",
        lambda path: (_ for _ in ()).throw(OSError("injected post-swap failure")),
    )
    with pytest.raises(ExceptionGroup, match="restore"):
        module._publish_artifacts(output, {name: {"new": name} for name in ARTIFACTS})
    assert list(tmp_path.glob(".audit.restore-failed-*"))


def test_post_swap_parent_fsync_failure_restores_old_pointer(tmp_path: Path, monkeypatch):
    module = load_cli_module()
    output = tmp_path / "audit"
    module._publish_artifacts(output, {name: {"old": name} for name in ARTIFACTS})
    old_target = os.readlink(output)
    real_replace = module.os.replace
    real_fsync = module._fsync_directory
    swapped = False
    failed = False

    def track_swap(source, destination):
        nonlocal swapped
        result = real_replace(source, destination)
        if Path(destination) == output:
            swapped = True
        return result

    def fail_commit_fsync(path):
        nonlocal failed
        if swapped and Path(path) == output.parent and not failed:
            failed = True
            raise OSError("injected post-swap parent fsync failure")
        return real_fsync(path)

    monkeypatch.setattr(module.os, "replace", track_swap)
    monkeypatch.setattr(module, "_fsync_directory", fail_commit_fsync)
    with pytest.raises(OSError, match="post-swap parent"):
        module._publish_artifacts(output, {name: {"new": name} for name in ARTIFACTS})
    assert os.readlink(output) == old_target
    module.load_artifact_set(output)


def test_loader_pins_open_generation_if_path_is_replaced(tmp_path: Path, monkeypatch):
    module = load_cli_module()
    output = tmp_path / "audit"
    module._publish_artifacts(output, {name: {"safe": name} for name in ARTIFACTS})
    expected = module.load_artifact_set(output)
    generation = output.resolve()
    real_open = module.os.open
    replaced = False

    def replace_after_open(path, flags, *args, **kwargs):
        nonlocal replaced
        fd = real_open(path, flags, *args, **kwargs)
        if not replaced and isinstance(path, str) and path == generation.name and kwargs.get("dir_fd") is not None:
            replaced = True
            moved = generation.with_name(generation.name + ".old")
            generation.rename(moved)
            generation.mkdir(mode=0o555)
        return fd

    monkeypatch.setattr(module.os, "open", replace_after_open)
    try:
        loaded = module.load_artifact_set(output)
        assert loaded == expected
    except ValueError as error:
        assert "changed" in str(error)


def test_main_reports_grouped_publication_failure_without_traceback(tmp_path: Path, monkeypatch, capsys):
    module = load_cli_module()
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, complete_draft(tmp_path))
    monkeypatch.setattr(
        module,
        "_publish_artifacts",
        lambda *args: (_ for _ in ()).throw(
            ExceptionGroup("controlled publication failure", [OSError("cleanup")])
        ),
    )

    assert module.main(["assess-feasibility", "--input", str(draft), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert "controlled publication failure" in captured.err
    assert "Traceback" not in captured.err


def test_main_does_not_swallow_keyboard_interrupt(tmp_path: Path, monkeypatch):
    module = load_cli_module()
    draft = tmp_path / "draft.yaml"
    write_yaml(draft, complete_draft(tmp_path))
    monkeypatch.setattr(module, "_publish_artifacts", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        module.main(["assess-feasibility", "--input", str(draft), "--output", str(tmp_path / "audit")])


def test_pointer_swap_failure_preserves_old_generation(tmp_path: Path, monkeypatch):
    module = load_cli_module()
    output = tmp_path / "audit"
    module._publish_artifacts(output, {name: {"old": name} for name in ARTIFACTS})
    old_target = output.resolve()
    real_replace = module.os.replace

    def fail_pointer(source, destination):
        if Path(destination) == output:
            raise OSError("injected pointer swap failure")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_pointer)
    with pytest.raises(OSError, match="pointer"):
        module._publish_artifacts(output, {name: {"new": name} for name in ARTIFACTS})
    assert output.resolve() == old_target
    module.load_artifact_set(output)
    assert not list((tmp_path / ".audit.generations").glob(".stage-*"))


def test_failure_before_generation_rename_preserves_old_pointer(tmp_path: Path, monkeypatch):
    module = load_cli_module()
    output = tmp_path / "audit"
    module._publish_artifacts(output, {name: {"old": name} for name in ARTIFACTS})
    old_target = output.resolve()
    monkeypatch.setattr(module, "_verify_published_set", lambda *a: (_ for _ in ()).throw(OSError("before rename")))
    with pytest.raises(OSError, match="before rename"):
        module._publish_artifacts(output, {name: {"new": name} for name in ARTIFACTS})
    assert output.resolve() == old_target
    assert not list((tmp_path / ".audit.generations").glob(".stage-*"))


def test_failure_after_generation_rename_before_pointer_preserves_old(tmp_path: Path, monkeypatch):
    module = load_cli_module()
    output = tmp_path / "audit"
    module._publish_artifacts(output, {name: {"old": name} for name in ARTIFACTS})
    old_target = output.resolve()
    real_symlink = module.os.symlink
    monkeypatch.setattr(module.os, "symlink", lambda *a: (_ for _ in ()).throw(OSError("after rename")))
    with pytest.raises(OSError, match="after rename"):
        module._publish_artifacts(output, {name: {"new": name} for name in ARTIFACTS})
    assert output.resolve() == old_target
    module.load_artifact_set(output)


def test_concurrent_publishers_leave_coherent_pointer(tmp_path: Path):
    module = load_cli_module()
    output = tmp_path / "audit"
    import threading
    errors=[]
    def publish(tag):
        try: module._publish_artifacts(output, {name: {tag: name} for name in ARTIFACTS})
        except Exception as e: errors.append(e)
    threads=[threading.Thread(target=publish,args=(tag,)) for tag in ("a","b")]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors
    module.load_artifact_set(output)
    assert output.is_symlink()


def test_minimal_missing_draft_writes_all_blocked_artifacts_without_results(tmp_path: Path):
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, {"experiment_id": "missing"})

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert {path.name for path in output.iterdir()} == ARTIFACTS
    inventory = load(output, "window_inventory.json")
    assert inventory["verdict"] == "BLOCKED_INSUFFICIENT_DATA"
    assert inventory["overall_status"] == "BLOCKED"
    assert not (tmp_path / "Results").exists()


def test_invalid_feature_values_are_counted_and_excluded_from_availability(tmp_path: Path):
    config = complete_draft(tmp_path)
    vix = Path(config["data_sources"]["VIX"]["path"])
    frame = pd.read_csv(vix)
    frame.loc[frame["date"] == "2022-01-03", "value"] = None
    frame.to_csv(vix, index=False)
    config["data_sources"]["VIX"]["maximum_staleness_days"] = 0
    config["data_sources"]["VIX"]["maximum_consecutive_gap_sessions"] = 0
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, config)

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    coverage = load(output, "data_coverage_audit.json")
    assert coverage["sources"]["VIX"]["invalid_value_count"] == 1
    inventory = load(output, "window_inventory.json")
    assert any(
        "UNAVAILABLE_SESSION_GAP:VIX" in reason
        for window in inventory["windows"]
        for reason in window["reasons"]
    )
def test_complete_four_source_data_passes_at_least_three_windows(tmp_path: Path):
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, complete_draft(tmp_path))

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 0, result.stderr
    inventory = load(output, "window_inventory.json")
    assert inventory["eligible_blind_window_count"] >= 3
    assert inventory["verdict"] == "PASS"
    assert [item["window_id"] for item in inventory["windows"]] == ["W1", "W2", "W3", "W4"]
    coverage = load(output, "data_coverage_audit.json")
    assert coverage["calendar_basis"]["source_id"] == "china_calendar"
    assert set(coverage["sources"]) == {"518880", "AU", "VIX", "DFII10"}
    assert all("sha256" in report for report in coverage["sources"].values())
    assert all("publication_lag" in report for report in coverage["sources"].values())
    assert load(output, "interface_readiness.json")["status"] == "NOT_ASSESSED"


def test_only_two_covered_windows_returns_blocked(tmp_path: Path):
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, complete_draft(tmp_path, start="2020-01-01", end="2025-12-31"))

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    inventory = load(output, "window_inventory.json")
    assert inventory["eligible_blind_window_count"] == 2
    assert inventory["verdict"] == "BLOCKED_INSUFFICIENT_TEST_WINDOWS"
    assert inventory["evaluation_status"] == "NOT_EVALUATED"
    assert inventory["proof_conclusion"] == "UNPROVEN"


def test_substituted_feature_identity_is_rejected(tmp_path: Path):
    config = complete_draft(tmp_path)
    config["data_sources"]["VIX"]["source_id"] = "OTHER"
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, config)

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    assert load(output, "data_coverage_audit.json")["status"] == "BLOCKED"


def test_invalid_g3_blocks_otherwise_complete_assessment(tmp_path: Path):
    config = complete_draft(tmp_path)
    config["g3_observability"]["minimum_required_generation_count"] = 2
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, config)

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    g3 = load(output, "g3_observability.json")
    inventory = load(output, "window_inventory.json")
    assert g3["status"] == "BLOCKED"
    assert inventory["overall_status"] == "BLOCKED"
    assert inventory["evaluation_status"] == "NOT_EVALUATED"
    assert inventory["proof_conclusion"] == "UNPROVEN"


def test_non_integer_minimum_generation_counts_block_overall_assessment(tmp_path: Path):
    for index, invalid in enumerate((3.0, True, "3")):
        case = tmp_path / f"case-{index}"
        case.mkdir()
        config = complete_draft(case)
        config["g3_observability"]["minimum_required_generation_count"] = invalid
        draft = case / "draft.yaml"
        output = case / "audit"
        write_yaml(draft, config)

        result = run_cli(draft, output, case)

        assert result.returncode == 2
        assert load(output, "g3_observability.json")["status"] == "BLOCKED"
        inventory = load(output, "window_inventory.json")
        assert inventory["overall_status"] == "BLOCKED"
        assert inventory["evaluation_status"] == "NOT_EVALUATED"
        assert inventory["proof_conclusion"] == "UNPROVEN"


def test_unknown_g3_policy_and_impossible_budget_are_rejected(tmp_path: Path):
    config = complete_draft(tmp_path)
    config["g3_observability"]["review_input"] = "other"
    config["g3_observability"]["failure_budget_policy"] = "other"
    config["g3_observability"]["per_window_candidate_budget"] = 5
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, config)

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    artifact = load(output, "g3_observability.json")
    assert artifact["status"] == "BLOCKED"
    assert artifact["review_input"] == "other"
    assert artifact["failure_budget_policy"] == "other"
    assert len(artifact["errors"]) == 3


def test_substituted_instrument_is_rejected(tmp_path: Path):
    config = complete_draft(tmp_path)
    config["data_sources"]["AU"]["instrument"] = "SILVER"
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, config)

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    assert load(output, "data_coverage_audit.json")["status"] == "BLOCKED"


def test_deleted_internal_518880_session_blocks_affected_windows(tmp_path: Path):
    config = complete_draft(tmp_path)
    path = Path(config["data_sources"]["518880"]["path"])
    frame = pd.read_parquet(path)
    frame = frame[frame["trade_date"] != 20220103]
    frame.to_parquet(path, index=False)
    config["data_sources"]["518880"]["maximum_consecutive_gap_sessions"] = 0
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, config)

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    inventory = load(output, "window_inventory.json")
    assert inventory["eligible_blind_window_count"] < 3
    assert any(
        "UNAVAILABLE_SESSION_GAP:518880" in reason
        for window in inventory["windows"]
        for reason in window["reasons"]
    )


def test_internal_calendar_date_gap_blocks_affected_windows(tmp_path: Path):
    config = complete_draft(tmp_path)
    calendar = Path(config["china_calendar"]["path"])
    frame = pd.read_csv(calendar)
    frame = frame[frame["cal_date"] != 20220108]  # closed Saturday still required inventory
    frame.to_csv(calendar, index=False)
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, config)

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    inventory = load(output, "window_inventory.json")
    assert any(
        reason == "INCOMPLETE_CALENDAR_INTERNAL:2022-01-08"
        for window in inventory["windows"]
        for reason in window["reasons"]
    )


def test_same_year_truncated_calendar_blocks_exact_fixed_intervals(tmp_path: Path):
    config = complete_draft(tmp_path)
    calendar = Path(config["china_calendar"]["path"])
    frame = pd.read_csv(calendar)
    frame = frame[(frame["cal_date"] >= 20180601) & (frame["cal_date"] <= 20250630)]
    frame.to_csv(calendar, index=False)
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, config)

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    inventory = load(output, "window_inventory.json")
    assert inventory["eligible_blind_window_count"] < 3
    assert any(
        reason.startswith("INCOMPLETE_CALENDAR_")
        for window in inventory["windows"]
        for reason in window["reasons"]
    )


def test_fund_nav_and_proxy_tradable_inputs_are_rejected(tmp_path: Path):
    for declaration in ("fund_nav", "proxy"):
        case = tmp_path / declaration
        case.mkdir()
        config = complete_draft(case)
        if declaration == "fund_nav":
            config["data_sources"]["518880"]["source_kind"] = declaration
        else:
            config["data_sources"]["518880"]["source_id"] = "prelisting_proxy"
        draft = case / "draft.yaml"
        output = case / "audit"
        write_yaml(draft, config)

        result = run_cli(draft, output, case)

        assert result.returncode == 2
        assert load(output, "data_coverage_audit.json")["status"] == "BLOCKED"


def test_unexpected_output_file_is_preserved_and_command_fails(tmp_path: Path):
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    write_yaml(draft, {"experiment_id": "missing"})

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert {path.name for path in output.iterdir()} == {"keep.txt"}


def test_rerun_atomically_replaces_known_artifacts_with_identical_bytes(tmp_path: Path):
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, complete_draft(tmp_path))

    first = run_cli(draft, output, tmp_path)
    first_bytes = {name: (output / name).read_bytes() for name in ARTIFACTS}
    second = run_cli(draft, output, tmp_path)

    assert first.returncode == second.returncode == 0
    assert first_bytes == {name: (output / name).read_bytes() for name in ARTIFACTS}
    assert {path.name for path in output.iterdir()} == ARTIFACTS


def test_invalid_yaml_has_no_traceback_and_writes_artifacts_when_output_known(tmp_path: Path):
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    draft.write_text("data_sources: [unterminated", encoding="utf-8")

    result = run_cli(draft, output, tmp_path)

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert {path.name for path in output.iterdir()} == ARTIFACTS
