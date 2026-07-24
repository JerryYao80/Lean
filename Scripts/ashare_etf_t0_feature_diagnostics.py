#!/usr/bin/env python3

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from ashare_etf_t0_feature_backtest import (  # noqa: E402
    BASE_SIGNAL_FIELDS,
    EXTENDED_SIGNAL_FIELDS,
    FEATURE_FIELD_LINES,
    build_etf_metadata_lookup,
    build_symbol_feature_frame,
    compute_cross_section_scores,
)
from ashare_etf_t0_monte_carlo import build_regime_frame  # noqa: E402
from tushare_data_layer import TushareDataLayer  # noqa: E402
from tushare_lean_export import load_registry_universe  # noqa: E402


PATH_KEYS = {
    "registry-file",
    "tushare-data-path",
    "dataset-catalog",
    "report-file",
    "json-report-file",
}
ALL_SIGNAL_FIELDS = [*BASE_SIGNAL_FIELDS, *EXTENDED_SIGNAL_FIELDS]
EPSILON = 1e-12


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    return {
        "registry-file": str(root / "Common" / "Securities" / "Equity" / "AShareETFMetadata.cs"),
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data_v2",
        "dataset-catalog": str(root / "Launcher" / "config" / "config-ashare-dataset-catalog.json"),
        "start-date": "20240101",
        "end-date": "20251231",
        "exclude-money-market-etfs": True,
        "min-symbol-history": 25,
        "min-cross-section-size": 10,
        "walk-forward-folds": 4,
        "walk-forward-min-train-days": 120,
        "walk-forward-min-test-days": 40,
        "whitelist-min-coverage-rate": 0.60,
        "whitelist-min-avg-cs-count": 20,
        "whitelist-min-ic-days": 60,
        "whitelist-min-abs-mean-ic": 0.020,
        "whitelist-min-wf-sign-match": 0.50,
        "default-min-coverage-rate": 0.85,
        "default-min-avg-cs-count": 50,
        "default-min-ic-days": 120,
        "default-min-abs-mean-ic": 0.030,
        "default-min-wf-sign-match": 0.75,
        "default-min-regime-alignment": 0.80,
        "default-max-base-score-corr": 0.35,
        "current-default-features": BASE_SIGNAL_FIELDS,
        "candidate-features": ALL_SIGNAL_FIELDS,
        "report-file": str(root / "Launcher" / "bin" / "Debug" / "AShareEtfT0FeatureIntradayAlgorithm-feature-diagnostics.md"),
        "json-report-file": str(root / "Launcher" / "bin" / "Debug" / "AShareEtfT0FeatureIntradayAlgorithm-feature-diagnostics.json"),
    }


def resolve_config_paths(config: dict, base_dir: Path) -> dict:
    resolved = dict(config)
    for key in PATH_KEYS:
        value = resolved.get(key)
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        resolved[key] = str(path)
    return resolved


def load_pipeline_config(config_path: str | Path | None = None, overrides: dict | None = None) -> dict:
    config = default_config()

    if config_path:
        path = Path(config_path).resolve()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        config.update(resolve_config_paths(loaded, path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    return resolve_config_paths(config, repo_root())


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _safe_float(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _rounded(value: float | None, digits: int = 6) -> float | None:
    number = _safe_float(value)
    return None if number is None else round(number, digits)


def _sign(value: float | None) -> int:
    number = _safe_float(value)
    if number is None or abs(number) <= EPSILON:
        return 0
    return 1 if number > 0 else -1


def _direction_label(weight_sign: int) -> str:
    if weight_sign < 0:
        return "negative"
    if weight_sign > 0:
        return "positive"
    return "neutral"


def parse_feature_definitions() -> dict[str, str]:
    definitions = {}
    for line in FEATURE_FIELD_LINES:
        text = line.strip()
        if not text.startswith("- ") or ":" not in text:
            continue
        name, description = text[2:].split(":", 1)
        definitions[f"signal_{name.strip()}"] = description.strip()
    return definitions


def build_feature_panel(config: dict) -> tuple[pd.DataFrame, dict]:
    data_layer = TushareDataLayer(config["tushare-data-path"], config["dataset-catalog"])
    metadata_lookup = build_etf_metadata_lookup(data_layer)
    universe = load_registry_universe(
        config["registry-file"],
        exclude_money_market=config.get("exclude-money-market-etfs", True),
    )

    prepared_frames = []
    min_symbol_history = int(config.get("min-symbol-history", 25) or 25)
    for symbol in universe:
        frame = build_symbol_feature_frame(
            data_layer,
            symbol,
            start_date=config.get("start-date"),
            end_date=config.get("end-date"),
            metadata_lookup=metadata_lookup,
        )
        if frame.empty or len(frame) < min_symbol_history:
            continue
        prepared_frames.append(frame)

    panel = pd.concat(prepared_frames, ignore_index=True) if prepared_frames else pd.DataFrame()
    summary = {
        "registry_universe": len(universe),
        "loaded_symbols": len(prepared_frames),
        "panel_rows": int(len(panel)),
        "trade_dates": int(panel["trade_date"].nunique()) if not panel.empty and "trade_date" in panel.columns else 0,
    }
    return panel, summary


def _daily_feature_table(panel: pd.DataFrame, feature: str, target_field: str, min_cross_section_size: int) -> pd.DataFrame:
    rows = []
    if panel.empty or feature not in panel.columns or target_field not in panel.columns:
        return pd.DataFrame(columns=["trade_date", "daily_count", "ic_count", "ic"])

    for trade_date, group in panel.groupby("trade_date", sort=True):
        feature_values = _numeric(group[feature])
        target_values = _numeric(group[target_field])
        valid_mask = feature_values.notna() & target_values.notna()
        daily_count = int(feature_values.notna().sum())
        ic_count = int(valid_mask.sum())
        ic_value = None
        if ic_count >= min_cross_section_size:
            ic_value = _safe_float(feature_values[valid_mask].corr(target_values[valid_mask], method="spearman"))
        rows.append(
            {
                "trade_date": str(trade_date),
                "daily_count": daily_count,
                "ic_count": ic_count,
                "ic": ic_value,
            }
        )

    return pd.DataFrame(rows)


def _daily_reference_correlation(
    panel: pd.DataFrame,
    feature: str,
    reference_field: str,
    min_cross_section_size: int,
) -> pd.DataFrame:
    rows = []
    if panel.empty or feature not in panel.columns or reference_field not in panel.columns:
        return pd.DataFrame(columns=["trade_date", "corr_count", "corr"])

    for trade_date, group in panel.groupby("trade_date", sort=True):
        left = _numeric(group[feature])
        right = _numeric(group[reference_field])
        valid_mask = left.notna() & right.notna()
        corr_count = int(valid_mask.sum())
        corr_value = None
        if corr_count >= min_cross_section_size:
            corr_value = _safe_float(left[valid_mask].corr(right[valid_mask], method="spearman"))
        rows.append({"trade_date": str(trade_date), "corr_count": corr_count, "corr": corr_value})

    return pd.DataFrame(rows)


def _summarize_regimes(daily_table: pd.DataFrame, regime_frame: pd.DataFrame) -> dict[str, dict]:
    if daily_table.empty or regime_frame.empty:
        return {}

    merged = daily_table.merge(regime_frame[["trade_date", "regime"]], on="trade_date", how="left")
    merged = merged.dropna(subset=["regime"])
    results = {}
    for regime, group in merged.groupby("regime", sort=True):
        ic_series = pd.to_numeric(group["ic"], errors="coerce").dropna()
        count_series = pd.to_numeric(group["ic_count"], errors="coerce").dropna()
        daily_count_series = pd.to_numeric(group["daily_count"], errors="coerce").dropna()
        results[str(regime)] = {
            "ic_days": int(ic_series.shape[0]),
            "mean_ic": _rounded(ic_series.mean()),
            "median_ic": _rounded(ic_series.median()),
            "abs_mean_ic": _rounded(ic_series.abs().mean()),
            "avg_ic_count": _rounded(count_series.mean()),
            "avg_daily_count": _rounded(daily_count_series.mean()),
        }
    return results


def _regime_alignment_stats(regimes: dict[str, dict], overall_ic_sign: int) -> tuple[float | None, int]:
    if overall_ic_sign == 0 or not regimes:
        return None, 0

    signs = []
    for details in regimes.values():
        if int(details.get("ic_days") or 0) <= 0:
            continue
        sign = _sign(details.get("mean_ic"))
        if sign == 0:
            continue
        signs.append(sign)

    if not signs:
        return None, 0

    aligned = sum(1 for sign in signs if sign == overall_ic_sign)
    return aligned / len(signs), len(signs) - aligned


def build_walk_forward_windows(trade_dates: list[str], folds: int, min_train_days: int, min_test_days: int) -> list[dict]:
    unique_dates = list(dict.fromkeys(trade_dates))
    total_dates = len(unique_dates)
    if folds <= 0 or total_dates < min_train_days + min_test_days:
        return []

    windows = []
    test_start = min_train_days
    while test_start < total_dates and len(windows) < folds:
        remaining_folds = folds - len(windows)
        remaining_dates = total_dates - test_start
        test_size = max(min_test_days, math.ceil(remaining_dates / remaining_folds))
        test_end = min(total_dates, test_start + test_size)
        if test_end - test_start < min_test_days:
            break
        windows.append(
            {
                "train_dates": unique_dates[:test_start],
                "test_dates": unique_dates[test_start:test_end],
            }
        )
        test_start = test_end
    return windows


def _mean_ic_for_dates(panel: pd.DataFrame, feature: str, trade_dates: list[str], min_cross_section_size: int) -> tuple[float | None, int]:
    subset = panel[panel["trade_date"].isin(trade_dates)].copy()
    daily = _daily_feature_table(subset, feature, "trade_return", min_cross_section_size)
    ic_series = pd.to_numeric(daily["ic"], errors="coerce").dropna()
    return _safe_float(ic_series.mean()), int(ic_series.shape[0])


def _build_walk_forward_summary(
    panel: pd.DataFrame,
    feature: str,
    windows: list[dict],
    min_cross_section_size: int,
    overall_ic_sign: int,
) -> dict:
    folds = []
    for index, window in enumerate(windows, start=1):
        train_mean_ic, train_ic_days = _mean_ic_for_dates(panel, feature, window["train_dates"], min_cross_section_size)
        test_mean_ic, test_ic_days = _mean_ic_for_dates(panel, feature, window["test_dates"], min_cross_section_size)
        train_sign = _sign(train_mean_ic)
        test_sign = _sign(test_mean_ic)
        sign_match = train_sign != 0 and train_sign == test_sign
        aligned_test = overall_ic_sign != 0 and test_sign == overall_ic_sign
        folds.append(
            {
                "fold": index,
                "train_start": window["train_dates"][0] if window["train_dates"] else None,
                "train_end": window["train_dates"][-1] if window["train_dates"] else None,
                "test_start": window["test_dates"][0] if window["test_dates"] else None,
                "test_end": window["test_dates"][-1] if window["test_dates"] else None,
                "train_ic_days": train_ic_days,
                "test_ic_days": test_ic_days,
                "train_mean_ic": _rounded(train_mean_ic),
                "test_mean_ic": _rounded(test_mean_ic),
                "sign_match": sign_match,
                "aligned_test": aligned_test,
            }
        )

    valid_folds = [
        fold
        for fold in folds
        if fold["train_mean_ic"] is not None and fold["test_mean_ic"] is not None and fold["test_ic_days"] > 0
    ]
    sign_match_rate = sum(1 for fold in valid_folds if fold["sign_match"]) / len(valid_folds) if valid_folds else 0.0
    aligned_test_rate = sum(1 for fold in valid_folds if fold["aligned_test"]) / len(valid_folds) if valid_folds else 0.0
    train_ic_values = [float(fold["train_mean_ic"]) for fold in valid_folds]
    test_ic_values = [float(fold["test_mean_ic"]) for fold in valid_folds]

    return {
        "fold_count": len(windows),
        "valid_fold_count": len(valid_folds),
        "sign_match_rate": _rounded(sign_match_rate),
        "aligned_test_rate": _rounded(aligned_test_rate),
        "train_mean_ic": _rounded(pd.Series(train_ic_values).mean()) if train_ic_values else None,
        "test_mean_ic": _rounded(pd.Series(test_ic_values).mean()) if test_ic_values else None,
        "folds": folds,
    }


def _passes_whitelist_thresholds(feature_report: dict, config: dict) -> bool:
    return (
        float(feature_report["coverage_rate"] or 0.0) >= float(config.get("whitelist-min-coverage-rate", 0.0) or 0.0)
        and float(feature_report["avg_ic_count"] or 0.0) >= float(config.get("whitelist-min-avg-cs-count", 0.0) or 0.0)
        and int(feature_report["ic_days"] or 0) >= int(config.get("whitelist-min-ic-days", 0) or 0)
        and float(feature_report["abs_mean_ic"] or 0.0) >= float(config.get("whitelist-min-abs-mean-ic", 0.0) or 0.0)
        and float(feature_report["walk_forward"]["sign_match_rate"] or 0.0) >= float(config.get("whitelist-min-wf-sign-match", 0.0) or 0.0)
    )


def _passes_default_thresholds(feature_report: dict, config: dict) -> bool:
    return (
        float(feature_report["coverage_rate"] or 0.0) >= float(config.get("default-min-coverage-rate", 0.0) or 0.0)
        and float(feature_report["avg_ic_count"] or 0.0) >= float(config.get("default-min-avg-cs-count", 0.0) or 0.0)
        and int(feature_report["ic_days"] or 0) >= int(config.get("default-min-ic-days", 0) or 0)
        and float(feature_report["abs_mean_ic"] or 0.0) >= float(config.get("default-min-abs-mean-ic", 0.0) or 0.0)
        and float(feature_report["walk_forward"]["sign_match_rate"] or 0.0) >= float(config.get("default-min-wf-sign-match", 0.0) or 0.0)
        and float(feature_report.get("regime_alignment_rate") or 0.0) >= float(config.get("default-min-regime-alignment", 0.0) or 0.0)
        and float(feature_report["base_score_corr_abs_mean"] or 0.0) <= float(config.get("default-max-base-score-corr", 1.0) or 1.0)
    )


def _build_reason_list(feature_report: dict, config: dict) -> list[str]:
    reasons = []
    coverage_value = float(feature_report.get("coverage_rate") or 0.0)
    coverage_threshold = float(config.get("whitelist-min-coverage-rate", 0.0) or 0.0)
    if coverage_value < coverage_threshold:
        reasons.append(f"low coverage ({coverage_value:.3f} < {coverage_threshold:.3f})")

    breadth_value = float(feature_report.get("avg_ic_count") or 0.0)
    breadth_threshold = float(config.get("whitelist-min-avg-cs-count", 0.0) or 0.0)
    if breadth_value < breadth_threshold:
        reasons.append(f"low breadth ({breadth_value:.3f} < {breadth_threshold:.3f})")

    sample_value = int(feature_report.get("ic_days") or 0)
    sample_threshold = int(config.get("whitelist-min-ic-days", 0) or 0)
    if sample_value < sample_threshold:
        reasons.append(f"low sample ({sample_value} < {sample_threshold})")

    ic_value = float(feature_report.get("abs_mean_ic") or 0.0)
    ic_threshold = float(config.get("whitelist-min-abs-mean-ic", 0.0) or 0.0)
    if ic_value < ic_threshold:
        reasons.append(f"low IC ({ic_value:.3f} < {ic_threshold:.3f})")

    redundancy_value = float(feature_report.get("base_score_corr_abs_mean") or 0.0)
    redundancy_threshold = float(config.get("default-max-base-score-corr", 1.0) or 1.0)
    if redundancy_value > redundancy_threshold:
        reasons.append(f"high redundancy vs base score ({redundancy_value:.3f} > {redundancy_threshold:.3f})")

    regime_alignment_value = float(feature_report.get("regime_alignment_rate") or 0.0)
    regime_alignment_threshold = float(config.get("default-min-regime-alignment", 0.0) or 0.0)
    if regime_alignment_value < regime_alignment_threshold:
        reasons.append(f"weak regime alignment ({regime_alignment_value:.3f} < {regime_alignment_threshold:.3f})")

    wf_value = float(feature_report["walk_forward"].get("sign_match_rate") or 0.0)
    wf_threshold = float(config.get("whitelist-min-wf-sign-match", 0.0) or 0.0)
    if wf_value < wf_threshold:
        reasons.append(f"weak walk-forward stability ({wf_value:.3f} < {wf_threshold:.3f})")
    return reasons


def _suggest_weight_range(feature_report: dict, is_current_default: bool) -> dict:
    mean_ic = _safe_float(feature_report.get("mean_ic"))
    ic_sign = _sign(mean_ic)
    weight_sign = ic_sign
    if weight_sign == 0:
        return {"min": 0.0, "max": 0.0}

    coverage_factor = min(1.0, float(feature_report.get("coverage_rate") or 0.0) / 0.90)
    breadth_factor = min(1.0, float(feature_report.get("avg_ic_count") or 0.0) / 60.0)
    stability_factor = 0.5 + 0.5 * float(feature_report["walk_forward"].get("sign_match_rate") or 0.0)
    redundancy = float(feature_report.get("base_score_corr_abs_mean") or 0.0)
    redundancy_factor = 1.0 - min(max(redundancy, 0.0), 0.90) * 0.35
    ic_strength = abs(float(mean_ic or 0.0))
    evidence = ic_strength * coverage_factor * breadth_factor * stability_factor * redundancy_factor
    multiplier = 3.4 if is_current_default else 2.8
    center_abs = min(0.35, max(0.0, evidence * multiplier))
    half_span = max(0.015, center_abs * 0.35)
    lower_abs = max(0.0, center_abs - half_span)
    upper_abs = min(0.35, center_abs + half_span)

    if weight_sign < 0:
        return {"min": _rounded(-upper_abs, 4), "max": _rounded(-lower_abs, 4)}
    return {"min": _rounded(lower_abs, 4), "max": _rounded(upper_abs, 4)}


def analyze_feature_panel(
    panel: pd.DataFrame,
    config: dict,
    candidate_features: list[str] | None = None,
    current_default_features: list[str] | None = None,
) -> dict:
    if panel.empty:
        return {
            "summary": {
                "current_default_features": current_default_features or list(config.get("current-default-features", BASE_SIGNAL_FIELDS)),
                "new_research_whitelist": [],
                "default_scoring_candidates": [],
                "rejected_features": [],
                "keep_current_default": True,
                "suggested_default_action": "keep_current_default_six_factor_model",
            },
            "features": {},
        }

    candidate_features = candidate_features or list(config.get("candidate-features", ALL_SIGNAL_FIELDS))
    current_default_features = current_default_features or list(config.get("current-default-features", BASE_SIGNAL_FIELDS))
    candidate_features = [feature for feature in candidate_features if feature in panel.columns]
    min_cross_section_size = int(config.get("min-cross-section-size", 10) or 10)

    total_trade_dates = int(panel["trade_date"].nunique())
    scored = compute_cross_section_scores(panel)
    base_score = scored[["trade_date", "symbol", "score"]].rename(columns={"score": "base_score"}) if not scored.empty else pd.DataFrame(columns=["trade_date", "symbol", "base_score"])
    panel_with_score = panel.merge(base_score, on=["trade_date", "symbol"], how="left")
    regime_frame = build_regime_frame(scored) if not scored.empty else pd.DataFrame(columns=["trade_date", "regime"])

    windows = build_walk_forward_windows(
        sorted(panel["trade_date"].astype(str).unique().tolist()),
        folds=int(config.get("walk-forward-folds", 4) or 4),
        min_train_days=int(config.get("walk-forward-min-train-days", 120) or 120),
        min_test_days=int(config.get("walk-forward-min-test-days", 40) or 40),
    )
    definitions = parse_feature_definitions()

    feature_reports = {}
    new_research_whitelist = []
    default_scoring_candidates = []
    rejected_features = []

    for feature in candidate_features:
        daily_table = _daily_feature_table(panel_with_score, feature, "trade_return", min_cross_section_size)
        ic_series = pd.to_numeric(daily_table["ic"], errors="coerce").dropna()
        daily_count_series = pd.to_numeric(daily_table["daily_count"], errors="coerce")
        ic_count_series = pd.to_numeric(daily_table["ic_count"], errors="coerce")
        coverage_days = int((daily_count_series > 0).sum())
        coverage_rate = coverage_days / total_trade_dates if total_trade_dates else 0.0
        mean_ic = _safe_float(ic_series.mean())
        overall_ic_sign = _sign(mean_ic)

        correlation_table = _daily_reference_correlation(panel_with_score, feature, "base_score", min_cross_section_size)
        corr_series = pd.to_numeric(correlation_table["corr"], errors="coerce").dropna()
        regime_summary = _summarize_regimes(daily_table, regime_frame)
        regime_alignment_rate, regime_flip_count = _regime_alignment_stats(regime_summary, overall_ic_sign)
        walk_forward = _build_walk_forward_summary(
            panel_with_score,
            feature,
            windows,
            min_cross_section_size=min_cross_section_size,
            overall_ic_sign=overall_ic_sign,
        )

        feature_report = {
            "definition": definitions.get(feature),
            "is_current_default": feature in current_default_features,
            "coverage_days": coverage_days,
            "coverage_rate": _rounded(coverage_rate),
            "avg_daily_count": _rounded(daily_count_series.mean()),
            "median_daily_count": _rounded(daily_count_series.median()),
            "ic_days": int(ic_series.shape[0]),
            "mean_ic": _rounded(mean_ic),
            "median_ic": _rounded(ic_series.median()),
            "abs_mean_ic": _rounded(ic_series.abs().mean()),
            "avg_ic_count": _rounded(ic_count_series[ic_count_series >= min_cross_section_size].mean()),
            "median_ic_count": _rounded(ic_count_series[ic_count_series >= min_cross_section_size].median()),
            "base_score_corr_mean": _rounded(corr_series.mean()),
            "base_score_corr_abs_mean": _rounded(corr_series.abs().mean()),
            "regime_alignment_rate": _rounded(regime_alignment_rate),
            "regime_flip_count": int(regime_flip_count),
            "suggested_direction": _direction_label(overall_ic_sign),
            "regimes": regime_summary,
            "walk_forward": walk_forward,
        }
        feature_report["suggested_weight_range"] = _suggest_weight_range(
            feature_report,
            is_current_default=feature_report["is_current_default"],
        )
        feature_report["passes_whitelist"] = _passes_whitelist_thresholds(feature_report, config)
        feature_report["passes_default"] = feature_report["passes_whitelist"] and _passes_default_thresholds(feature_report, config)
        feature_report["reasons"] = _build_reason_list(feature_report, config)

        if feature_report["is_current_default"]:
            feature_report["recommendation"] = "current_default"
        elif feature_report["passes_default"]:
            feature_report["recommendation"] = "default_candidate"
            default_scoring_candidates.append(feature)
            new_research_whitelist.append(feature)
        elif feature_report["passes_whitelist"]:
            feature_report["recommendation"] = "research_whitelist"
            new_research_whitelist.append(feature)
        else:
            feature_report["recommendation"] = "exclude"
            rejected_features.append(feature)

        feature_reports[feature] = feature_report

    for feature in current_default_features:
        if feature not in feature_reports:
            continue
        if feature not in rejected_features:
            continue
        rejected_features.remove(feature)

    summary = {
        "current_default_features": [feature for feature in current_default_features if feature in feature_reports],
        "new_research_whitelist": new_research_whitelist,
        "default_scoring_candidates": default_scoring_candidates,
        "rejected_features": rejected_features,
        "keep_current_default": len(default_scoring_candidates) == 0,
        "suggested_default_action": (
            "keep_current_default_six_factor_model"
            if len(default_scoring_candidates) == 0
            else "incrementally_promote_default_candidates"
        ),
    }

    return {"summary": summary, "features": feature_reports}


def build_report_markdown(report: dict, config: dict, panel_summary: dict) -> str:
    summary = report["summary"]
    feature_items = sorted(
        report["features"].items(),
        key=lambda item: (
            0 if item[1].get("recommendation") == "current_default" else 1,
            -abs(float(item[1].get("mean_ic") or 0.0)),
            item[0],
        ),
    )

    lines = [
        "# AShare ETF T+0 Feature Diagnostics",
        "",
        "## Universe",
        f"- Start Date: {config.get('start-date')}",
        f"- End Date: {config.get('end-date')}",
        f"- Registry Universe: {panel_summary.get('registry_universe', 0)}",
        f"- Loaded Symbols: {panel_summary.get('loaded_symbols', 0)}",
        f"- Panel Rows: {panel_summary.get('panel_rows', 0)}",
        f"- Trade Dates: {panel_summary.get('trade_dates', 0)}",
        "",
        "## Screening Summary",
        f"- Current Default Features: {', '.join(summary['current_default_features']) or 'None'}",
        f"- New Research Whitelist: {', '.join(summary['new_research_whitelist']) or 'None'}",
        f"- Default Scoring Candidates: {', '.join(summary['default_scoring_candidates']) or 'None'}",
        f"- Rejected Features: {', '.join(summary['rejected_features']) or 'None'}",
        f"- Suggested Default Action: {summary['suggested_default_action']}",
        "",
        "## Thresholds",
        f"- IC min cross-section: {config.get('min-cross-section-size')}",
        f"- Whitelist coverage / breadth / |IC| / WF sign match: {config.get('whitelist-min-coverage-rate')} / {config.get('whitelist-min-avg-cs-count')} / {config.get('whitelist-min-abs-mean-ic')} / {config.get('whitelist-min-wf-sign-match')}",
        f"- Default coverage / breadth / |IC| / WF sign match / regime alignment / max base corr: {config.get('default-min-coverage-rate')} / {config.get('default-min-avg-cs-count')} / {config.get('default-min-abs-mean-ic')} / {config.get('default-min-wf-sign-match')} / {config.get('default-min-regime-alignment')} / {config.get('default-max-base-score-corr')}",
        "",
        "## Feature Details",
    ]

    for feature, details in feature_items:
        weight_range = details["suggested_weight_range"]
        lines.extend(
            [
                f"### {feature}",
                f"- Recommendation: {details['recommendation']}",
                f"- Definition: {details.get('definition') or 'n/a'}",
                f"- Coverage Days / Rate: {details['coverage_days']} / {details['coverage_rate']}",
                f"- Daily Count avg / median: {details['avg_daily_count']} / {details['median_daily_count']}",
                f"- IC Days: {details['ic_days']}",
                f"- Mean / Median / |Mean| IC: {details['mean_ic']} / {details['median_ic']} / {details['abs_mean_ic']}",
                f"- IC Breadth avg / median: {details['avg_ic_count']} / {details['median_ic_count']}",
                f"- Base Score Corr mean / abs mean: {details['base_score_corr_mean']} / {details['base_score_corr_abs_mean']}",
                f"- Regime Alignment Rate / Flip Count: {details.get('regime_alignment_rate')} / {details.get('regime_flip_count')}",
                f"- Suggested Direction: {details['suggested_direction']}",
                f"- Suggested Weight Range: [{weight_range['min']}, {weight_range['max']}]",
                f"- Walk-Forward valid folds / sign match / aligned test: {details['walk_forward']['valid_fold_count']} / {details['walk_forward']['sign_match_rate']} / {details['walk_forward']['aligned_test_rate']}",
            ]
        )
        if details["reasons"]:
            lines.append(f"- Notes: {'; '.join(details['reasons'])}")
        if details["regimes"]:
            regime_parts = []
            for regime, regime_details in details["regimes"].items():
                regime_parts.append(
                    f"{regime}: mean_ic={regime_details['mean_ic']}, ic_days={regime_details['ic_days']}, avg_ic_count={regime_details['avg_ic_count']}"
                )
            lines.append(f"- Regimes: {' | '.join(regime_parts)}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def run_diagnostics(config: dict) -> dict:
    panel, panel_summary = build_feature_panel(config)
    report = analyze_feature_panel(panel, config)
    report["panel_summary"] = panel_summary
    report["config_snapshot"] = {
        key: value
        for key, value in config.items()
        if key not in {"registry-file", "tushare-data-path", "dataset-catalog", "report-file", "json-report-file"}
    }

    markdown = build_report_markdown(report, config, panel_summary)
    report_path = Path(config["report-file"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown, encoding="utf-8")

    json_path = Path(config["json-report-file"])
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="Launcher/config/config-ashare-etf-t0-feature-diagnostics.json")
    parser.add_argument("--report-file")
    parser.add_argument("--json-report-file")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--min-cross-section-size", type=int)
    parser.add_argument("--walk-forward-folds", type=int)
    parser.add_argument("--walk-forward-min-train-days", type=int)
    parser.add_argument("--walk-forward-min-test-days", type=int)
    parser.add_argument("--include-money-market-etfs", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    overrides = {
        "report-file": args.report_file,
        "json-report-file": args.json_report_file,
        "start-date": args.start_date,
        "end-date": args.end_date,
        "min-cross-section-size": args.min_cross_section_size,
        "walk-forward-folds": args.walk_forward_folds,
        "walk-forward-min-train-days": args.walk_forward_min_train_days,
        "walk-forward-min-test-days": args.walk_forward_min_test_days,
    }
    config = load_pipeline_config(args.config, overrides=overrides)
    config["exclude-money-market-etfs"] = not args.include_money_market_etfs
    run_diagnostics(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
