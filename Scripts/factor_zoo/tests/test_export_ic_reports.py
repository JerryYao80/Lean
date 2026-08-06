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
    assert data["ic_window_end"] == "2024-01-31"
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


def test_expanding_window_end_equals_month_end_no_lookahead(tmp_path, monkeypatch):
    """compute_ic_report is called with end_date=month_end — no future data leaks in."""
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
    assert captured["end"] == "2024-06-30", "window end must equal month_end (no lookahead)"
