from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
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


def test_every_artifact_has_matching_generation_and_artifact_set_hash(tmp_path: Path):
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "audit"
    write_yaml(draft, complete_draft(tmp_path))

    assert run_cli(draft, output, tmp_path).returncode == 0

    assert_coherent_artifact_set(output)
    assert {path.name for path in output.iterdir()} == ARTIFACTS
    assert not list(tmp_path.glob(".audit.stage-*"))
    assert not list(tmp_path.glob(".audit.backup-*"))


def test_injected_replace_failure_restores_complete_old_generation(tmp_path: Path, monkeypatch):
    module = load_cli_module()
    output = tmp_path / "audit"
    output.mkdir()
    old = {name: {"old": name} for name in ARTIFACTS}
    module._publish_artifacts(output, old)
    old_bytes = {name: (output / name).read_bytes() for name in ARTIFACTS}
    replacements = 0
    real_replace = module.os.replace

    def fail_second_publish(source, destination):
        nonlocal replacements
        if Path(source).parent.name.startswith(".audit.stage-"):
            replacements += 1
            if replacements == 2:
                raise OSError("injected replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_second_publish)
    with pytest.raises(OSError, match="injected"):
        module._publish_artifacts(output, {name: {"new": name} for name in ARTIFACTS})

    assert old_bytes == {name: (output / name).read_bytes() for name in ARTIFACTS}
    assert_coherent_artifact_set(output)
    assert not list(tmp_path.glob(".audit.stage-*"))
    assert not list(tmp_path.glob(".audit.backup-*"))


def test_concurrent_publishers_leave_one_coherent_generation(tmp_path: Path):
    draft_a = tmp_path / "a.yaml"
    draft_b = tmp_path / "b.yaml"
    output = tmp_path / "audit"
    config_a = complete_draft(tmp_path / "a-data")
    config_b = complete_draft(tmp_path / "b-data")
    write_yaml(draft_a, config_a)
    write_yaml(draft_b, config_b)
    command = lambda draft: [
        sys.executable, str(CLI), "assess-feasibility", "--input", str(draft),
        "--output", str(output),
    ]

    first = subprocess.Popen(command(draft_a), cwd=tmp_path)
    second = subprocess.Popen(command(draft_b), cwd=tmp_path)
    returncodes = [first.wait(), second.wait()]

    assert returncodes == [0, 0]
    assert_coherent_artifact_set(output)
    assert {path.name for path in output.iterdir()} == ARTIFACTS
    assert not list(tmp_path.glob(".audit.stage-*"))
    assert not list(tmp_path.glob(".audit.backup-*"))


def test_unexpected_file_racing_publication_is_preserved_and_rejected(tmp_path: Path, monkeypatch):
    module = load_cli_module()
    output = tmp_path / "audit"
    output.mkdir()
    sentinel = output / "raced.txt"
    real_validate = module._validate_output

    def race_after_validation(path):
        real_validate(path)
        sentinel.write_text("preserve", encoding="utf-8")

    monkeypatch.setattr(module, "_validate_output", race_after_validation)
    with pytest.raises(ValueError, match="unexpected"):
        module._publish_artifacts(output, {name: {"new": name} for name in ARTIFACTS})

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert {path.name for path in output.iterdir()} == {"raced.txt"}


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
