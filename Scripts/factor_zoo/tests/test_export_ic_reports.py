"""Tests for export_ic_reports.py — expanding-window IC report JSON generation."""
import json
import sys
from pathlib import Path

import pytest

# Make Scripts/factor_zoo importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_fake_engine(monkeypatch, report_rows):
    """Patch ICIREngine so compute_ic_report returns a fixed DataFrame
    without touching real parquet data."""
    import pandas as pd
    from export_ic_reports import ICIREngine

    fake_df = pd.DataFrame(report_rows)
    monkeypatch.setattr(ICIREngine, "compute_ic_report",
                        lambda self, start_date, end_date, horizon=21, factor_ids=None: fake_df)


def test_export_single_month_end_produces_valid_json(tmp_path, monkeypatch):
    """A single month-end export produces a JSON file matching the schema."""
    from export_ic_reports import export_ic_reports

    report_rows = [
        {"factor_id": "alpha012", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
         "rank_ic_mean": 0.05, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12},
        {"factor_id": "alpha077", "ic_mean": 0.04, "ic_std": 0.11, "ic_ir": 0.36,
         "rank_ic_mean": 0.048, "rank_ic_std": 0.12, "rank_ic_ir": 0.39, "n_obs": 12},
    ]
    _make_fake_engine(monkeypatch, report_rows)

    out_dir = tmp_path / "ic-reports"
    export_ic_reports(
        month_ends=["2024-01-31"],
        ic_window_start="2020-01-02",
        horizon=21,
        min_ic=0.02,
        min_ir=0.3,
        out_dir=str(out_dir),
    )

    json_path = out_dir / "ic_report_2024-01-31.json"
    assert json_path.exists(), f"Expected JSON at {json_path}"
    data = json.loads(json_path.read_text())
    assert data["report_date"] == "2024-01-31"
    assert data["ic_window_start"] == "2020-01-02"
    assert data["ic_window_end"] == "2024-01-30"
    assert data["horizon"] == 21
    assert data["min_ic"] == 0.02
    assert data["min_ir"] == 0.3
    assert isinstance(data["factors"], list)
    assert len(data["factors"]) == 2
    f0 = data["factors"][0]
    assert set(f0.keys()) == {"factor_id", "rank_ic_mean", "rank_ic_ir", "weight"}
    assert f0["factor_id"] == "alpha012"


def test_weights_are_normalized_to_sum_one(tmp_path, monkeypatch):
    """Factor weights in each report sum to 1.0."""
    from export_ic_reports import export_ic_reports

    report_rows = [
        {"factor_id": "alpha012", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
         "rank_ic_mean": 0.060, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12},
        {"factor_id": "alpha077", "ic_mean": 0.04, "ic_std": 0.11, "ic_ir": 0.36,
         "rank_ic_mean": 0.040, "rank_ic_std": 0.12, "rank_ic_ir": 0.39, "n_obs": 12},
        {"factor_id": "alpha001", "ic_mean": 0.03, "ic_std": 0.10, "ic_ir": 0.30,
         "rank_ic_mean": 0.030, "rank_ic_std": 0.11, "rank_ic_ir": 0.31, "n_obs": 12},
    ]
    _make_fake_engine(monkeypatch, report_rows)

    out_dir = tmp_path / "ic-reports"
    export_ic_reports(
        month_ends=["2024-03-31"],
        ic_window_start="2020-01-02",
        out_dir=str(out_dir),
    )
    data = json.loads((out_dir / "ic_report_2024-03-31.json").read_text())
    total = sum(f["weight"] for f in data["factors"])
    assert abs(total - 1.0) < 1e-9, f"weights sum to {total}, expected 1.0"


def test_idempotent_skip_existing(tmp_path, monkeypatch):
    """Re-running without --force skips existing JSON files (no recompute)."""
    from export_ic_reports import export_ic_reports

    report_rows = [
        {"factor_id": "alpha012", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
         "rank_ic_mean": 0.052, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12},
    ]
    _make_fake_engine(monkeypatch, report_rows)

    out_dir = tmp_path / "ic-reports"
    export_ic_reports(month_ends=["2024-01-31"], ic_window_start="2020-01-02",
                      out_dir=str(out_dir))

    # Second run without force — should NOT call compute_ic_report.
    call_count = {"n": 0}
    import export_ic_reports as mod
    orig = mod.ICIREngine.compute_ic_report
    def counting(self, *a, **k):
        call_count["n"] += 1
        return orig(self, *a, **k)
    monkeypatch.setattr(mod.ICIREngine, "compute_ic_report", counting)

    export_ic_reports(month_ends=["2024-01-31"], ic_window_start="2020-01-02",
                      out_dir=str(out_dir))
    assert call_count["n"] == 0, "should skip existing without force"


def test_expanding_window_end_is_day_before_month_end_no_lookahead(tmp_path, monkeypatch):
    """compute_ic_report is called with end_date=month_end - 1 day — no future data leaks in."""
    from export_ic_reports import export_ic_reports

    report_rows = [
        {"factor_id": "alpha012", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
         "rank_ic_mean": 0.052, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12},
    ]
    captured = {}
    import export_ic_reports as mod
    def capture(self, start_date, end_date, horizon=21, factor_ids=None):
        captured["start"] = start_date
        captured["end"] = end_date
        captured["horizon"] = horizon
        import pandas as pd
        return pd.DataFrame(report_rows)
    monkeypatch.setattr(mod.ICIREngine, "compute_ic_report", capture)

    export_ic_reports(month_ends=["2024-06-30"], ic_window_start="2020-01-02",
                      out_dir=str(tmp_path / "ic-reports"))
    assert captured["start"] == "2020-01-02"
    assert captured["end"] == "2024-06-29", "window end must be day before month_end (no lookahead)"


def test_month_end_dates_handles_december_rollover_and_midmonth_end():
    from export_ic_reports import _month_end_dates
    dates = _month_end_dates("2023-12-01", "2024-02-15")
    assert "2023-12-31" in dates
    assert "2024-01-31" in dates
    # mid-month end: Feb 2024 month-end (29th, leap year) > 2024-02-15 -> skipped
    assert "2024-02-29" not in dates
    # start on a month-end is included
    dates2 = _month_end_dates("2024-01-31", "2024-01-31")
    assert dates2 == ["2024-01-31"]


def test_force_overwrites_existing_json(tmp_path, monkeypatch):
    from export_ic_reports import export_ic_reports
    rows1 = [{"factor_id": "alpha012", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
              "rank_ic_mean": 0.052, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12}]
    _make_fake_engine(monkeypatch, rows1)
    out_dir = tmp_path / "ic-reports"
    export_ic_reports(month_ends=["2024-01-31"], ic_window_start="2020-01-02", out_dir=str(out_dir))
    first = (out_dir / "ic_report_2024-01-31.json").read_text()
    # second run with force + different data
    rows2 = [{"factor_id": "alpha099", "ic_mean": 0.06, "ic_std": 0.13, "ic_ir": 0.45,
              "rank_ic_mean": 0.061, "rank_ic_std": 0.14, "rank_ic_ir": 0.44, "n_obs": 12}]
    _make_fake_engine(monkeypatch, rows2)
    export_ic_reports(month_ends=["2024-01-31"], ic_window_start="2020-01-02", out_dir=str(out_dir), force=True)
    second = (out_dir / "ic_report_2024-01-31.json").read_text()
    assert first != second
    import json as _j
    assert _j.loads(second)["factors"][0]["factor_id"] == "alpha099"


def test_min_ic_min_ir_filter_factors(tmp_path, monkeypatch):
    """Factors below min_ic/min_ir thresholds are excluded."""
    from export_ic_reports import export_ic_reports
    rows = [
        {"factor_id": "good_ic", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
         "rank_ic_mean": 0.052, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12},
        {"factor_id": "low_ic", "ic_mean": 0.001, "ic_std": 0.12, "ic_ir": 0.01,
         "rank_ic_mean": 0.001, "rank_ic_std": 0.13, "rank_ic_ir": 0.01, "n_obs": 12},  # below 0.02
        {"factor_id": "low_ir", "ic_mean": 0.05, "ic_std": 0.30, "ic_ir": 0.17,
         "rank_ic_mean": 0.05, "rank_ic_std": 0.30, "rank_ic_ir": 0.17, "n_obs": 12},  # below 0.3
    ]
    _make_fake_engine(monkeypatch, rows)
    out_dir = tmp_path / "ic-reports"
    export_ic_reports(month_ends=["2024-01-31"], ic_window_start="2020-01-02",
                      min_ic=0.02, min_ir=0.3, out_dir=str(out_dir))
    data = json.loads((out_dir / "ic_report_2024-01-31.json").read_text())
    ids = [f["factor_id"] for f in data["factors"]]
    assert ids == ["good_ic"], f"only good_ic should pass filter, got {ids}"


def test_force_with_empty_payload_deletes_stale_file(tmp_path, monkeypatch):
    """force=True + no factors pass filter -> old JSON is deleted, not left stale."""
    from export_ic_reports import export_ic_reports
    good = [{"factor_id": "alpha012", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
             "rank_ic_mean": 0.052, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12}]
    _make_fake_engine(monkeypatch, good)
    out_dir = tmp_path / "ic-reports"
    export_ic_reports(month_ends=["2024-01-31"], ic_window_start="2020-01-02", out_dir=str(out_dir))
    assert (out_dir / "ic_report_2024-01-31.json").exists()
    # now force recompute with a report where NO factors pass
    import pandas as pd
    import export_ic_reports as mod
    monkeypatch.setattr(mod.ICIREngine, "compute_ic_report",
                        lambda self, *a, **k: pd.DataFrame(columns=["factor_id", "rank_ic_mean", "rank_ic_ir"]))
    export_ic_reports(month_ends=["2024-01-31"], ic_window_start="2020-01-02",
                      out_dir=str(out_dir), force=True)
    assert not (out_dir / "ic_report_2024-01-31.json").exists(), "stale file should be deleted"
