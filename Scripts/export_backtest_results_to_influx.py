from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request


DEFAULT_ALGORITHM_ID = "AShareBarraCNE5Algorithm"
DEFAULT_MODE = "backtesting"
DEFAULT_STAT_MEASUREMENT = "lean_backtest_stat"
DEFAULT_MONTE_CARLO_MEASUREMENT = "lean_monte_carlo"
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

PERCENT_METRIC_PATTERNS = (
    "return",
    "drawdown",
    "probability",
    "rate",
    "profit",
    "turnover",
    "ratio",
    "standarddeviation",
    "variance",
    "trackingerror",
    "valueatrisk",
    "var_",
    "cvar_",
)
NON_PERCENT_METRIC_PATTERNS = (
    "sharpe",
    "sortino",
    "calmar",
    "treynor",
    "information",
    "profitlossratio",
    "profit-loss ratio",
    "expectancy",
    "equity",
    "orders",
    "trades",
    "days",
    "trials",
    "fees",
    "capacity",
    "rebalances",
    "finalequity",
)


@dataclass(frozen=True)
class InfluxPoint:
    measurement: str
    tags: dict[str, str]
    fields: dict[str, float | str]
    timestamp_ns: int


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_summary_file() -> Path:
    return repo_root() / "Results" / f"{DEFAULT_ALGORITHM_ID}-summary.json"


def default_monte_carlo_files() -> list[Path]:
    root = repo_root()
    return [
        root / "Results" / f"{DEFAULT_ALGORITHM_ID}-monte-carlo.json",
        root / "Results" / "barra-cne5-monte-carlo-report.json",
    ]


def parse_datetime_ns(value, fallback: datetime | None = None) -> int:
    if value:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    else:
        parsed = fallback or datetime.now(timezone.utc)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def sanitize_number_text(value: str) -> str:
    text = value.strip()
    text = text.replace("¥", "").replace("￥", "")
    text = text.replace(",", "").replace(" ", "")
    text = text.replace("%", "")
    return text


def parse_numeric_value(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None

    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"nan", "none", "null", "n/a", "na"}:
        return None

    cleaned = sanitize_number_text(text)
    if not cleaned:
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def metric_key(metric: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "", metric.lower().replace(" ", "").replace("-", ""))


def is_percent_metric(metric: str) -> bool:
    key = metric_key(metric)
    lowered = metric.lower()
    if "probabilistic" in key and "sharpe" in key:
        return True
    if any(pattern in key or pattern in lowered for pattern in NON_PERCENT_METRIC_PATTERNS):
        return False
    return any(pattern in key or pattern in lowered for pattern in PERCENT_METRIC_PATTERNS)


def value_unit(metric: str, raw_value=None) -> str:
    text = "" if raw_value is None else str(raw_value)
    lowered = metric.lower()
    if "%" in text or is_percent_metric(metric):
        return "percent"
    if "¥" in text or "￥" in text or "equity" in lowered or "fees" in lowered or "capacity" in lowered:
        return "currency"
    if "orders" in lowered or "trades" in lowered or "trials" in lowered or "days" in lowered or "rebalances" in lowered:
        return "count"
    return "number"


def normalize_numeric_for_display(metric: str, value: float | None, raw_value=None) -> float | None:
    if value is None:
        return None
    if "%" in str(raw_value):
        return value
    if is_percent_metric(metric) and abs(value) <= 1.0:
        return value * 100.0
    return value


def get_case_insensitive(mapping: dict, *keys: str):
    for key in keys:
        if key in mapping:
            return mapping[key]
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def infer_run_id(summary: dict, explicit_run_id: str | None = None) -> str:
    if explicit_run_id:
        return explicit_run_id
    state = get_case_insensitive(summary, "state") or {}
    name = get_case_insensitive(state, "Name", "name")
    start_time = get_case_insensitive(state, "StartTime", "startTime")
    if name and start_time:
        return f"{name}-{str(start_time).replace(':', '').replace('-', '').replace('T', '-').replace('Z', '')}"
    if name:
        return str(name)
    return "local-backtest"


def build_stat_points(
    summary: dict,
    algorithm_id: str,
    run_id: str,
    timestamp_ns: int,
    measurement: str = DEFAULT_STAT_MEASUREMENT,
    mode: str = DEFAULT_MODE,
) -> list[InfluxPoint]:
    points: list[InfluxPoint] = []
    sections = [
        ("statistics", get_case_insensitive(summary, "Statistics", "statistics") or {}),
        ("runtime", get_case_insensitive(summary, "RuntimeStatistics", "runtimeStatistics") or {}),
        ("portfolio", (get_case_insensitive(summary, "TotalPerformance", "totalPerformance") or {}).get("portfolioStatistics", {})),
    ]

    for category, values in sections:
        if not isinstance(values, dict):
            continue
        for metric, raw_value in values.items():
            numeric = normalize_numeric_for_display(str(metric), parse_numeric_value(raw_value), raw_value=raw_value)
            fields: dict[str, float | str] = {"text_value": str(raw_value)}
            if numeric is not None:
                fields["numeric_value"] = numeric

            points.append(
                InfluxPoint(
                    measurement=measurement,
                    tags={
                        "algorithm_id": algorithm_id,
                        "mode": mode,
                        "run_id": run_id,
                        "category": category,
                        "metric": str(metric),
                        "unit": value_unit(str(metric), raw_value),
                    },
                    fields=fields,
                    timestamp_ns=timestamp_ns,
                )
            )
    return points


def normalize_monte_carlo_metric(metric: str, value) -> tuple[float | None, str]:
    numeric = parse_numeric_value(value)
    unit = value_unit(metric, value)
    if numeric is not None and is_percent_metric(metric) and abs(numeric) <= 1.0:
        numeric *= 100.0
        unit = "percent"
    return numeric, unit


def monte_carlo_source_name(path: Path, payload: dict) -> str:
    if "base_backtest" in payload:
        return "pipeline_monte_carlo"
    algo_monte_carlo_names = (
        f"{DEFAULT_ALGORITHM_ID}-monte-carlo.json",
        "AShareSectorSmallCapAlgorithm-monte-carlo.json",
    )
    if path.name in algo_monte_carlo_names:
        return "algorithm_monte_carlo"
    return path.stem


def build_monte_carlo_points(
    payload: dict,
    source_file: Path,
    algorithm_id: str,
    run_id: str,
    timestamp_ns: int,
    measurement: str = DEFAULT_MONTE_CARLO_MEASUREMENT,
    mode: str = DEFAULT_MODE,
) -> list[InfluxPoint]:
    scenarios = payload.get("scenarios") or {}
    if not isinstance(scenarios, dict):
        return []

    source = monte_carlo_source_name(source_file, payload)
    points: list[InfluxPoint] = []
    for scenario, metrics in scenarios.items():
        if not isinstance(metrics, dict):
            continue
        for metric, raw_value in metrics.items():
            if metric == "scenario":
                continue
            numeric, unit = normalize_monte_carlo_metric(str(metric), raw_value)
            fields: dict[str, float | str] = {"text_value": str(raw_value)}
            if numeric is not None:
                fields["numeric_value"] = numeric

            points.append(
                InfluxPoint(
                    measurement=measurement,
                    tags={
                        "algorithm_id": algorithm_id,
                        "mode": mode,
                        "run_id": run_id,
                        "source": source,
                        "scenario": str(scenario),
                        "metric": str(metric),
                        "unit": unit,
                    },
                    fields=fields,
                    timestamp_ns=timestamp_ns,
                )
            )
    return points


def escape_key(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def escape_string_field(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def format_field_value(value: float | str) -> str:
    if isinstance(value, str):
        return escape_string_field(value)
    return format(float(value), ".12g")


def point_to_line_protocol(point: InfluxPoint) -> str:
    tag_set = ",".join(f"{escape_key(key)}={escape_key(str(value))}" for key, value in sorted(point.tags.items()))
    field_set = ",".join(f"{escape_key(key)}={format_field_value(value)}" for key, value in point.fields.items())
    return f"{escape_key(point.measurement)},{tag_set} {field_set} {point.timestamp_ns}"


def write_lines_to_influx(
    lines: Iterable[str],
    influx_url: str,
    org: str,
    bucket: str,
    token: str,
    timeout_seconds: float = 30.0,
) -> int:
    payload_lines = [line for line in lines if line]
    if not payload_lines:
        return 0

    query = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    url = f"{influx_url.rstrip('/')}/api/v2/write?{query}"
    influx_request = request.Request(
        url,
        data=("\n".join(payload_lines) + "\n").encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )

    try:
        with request.urlopen(influx_request, timeout=timeout_seconds) as response:
            if 200 <= response.status < 300:
                return len(payload_lines)
            detail = response.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"InfluxDB write failed with HTTP {response.status}: {detail}")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"InfluxDB write failed with HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"InfluxDB write failed: {exc}") from exc


def resolve_influx_token(token: str | None) -> str:
    resolved = str(token or os.environ.get("INFLUXDB_TOKEN") or "").strip()
    if not resolved:
        raise ValueError("InfluxDB token is required. Set INFLUXDB_TOKEN or pass --token.")
    return resolved


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def collect_points(
    summary_file: str | Path,
    monte_carlo_files: list[str | Path],
    algorithm_id: str,
    run_id: str | None = None,
    mode: str = DEFAULT_MODE,
) -> list[InfluxPoint]:
    summary_path = Path(summary_file)
    summary = load_json(summary_path)
    resolved_run_id = infer_run_id(summary, explicit_run_id=run_id)
    state = get_case_insensitive(summary, "state") or {}
    timestamp_ns = parse_datetime_ns(
        get_case_insensitive(state, "EndTime", "endTime") or get_case_insensitive(summary, "generatedAtUtc", "GeneratedAtUtc")
    )

    points = build_stat_points(
        summary=summary,
        algorithm_id=algorithm_id,
        run_id=resolved_run_id,
        timestamp_ns=timestamp_ns,
        mode=mode,
    )

    for monte_carlo_file in monte_carlo_files:
        path = Path(monte_carlo_file)
        if not path.exists():
            continue
        payload = load_json(path)
        mc_timestamp_ns = parse_datetime_ns(payload.get("generatedAtUtc") or payload.get("generated_at_utc"), fallback=datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc))
        points.extend(
            build_monte_carlo_points(
                payload=payload,
                source_file=path,
                algorithm_id=algorithm_id,
                run_id=resolved_run_id,
                timestamp_ns=mc_timestamp_ns,
                mode=mode,
            )
        )
    return points


def build_summary(points: list[InfluxPoint], dry_run: bool, written: int) -> dict:
    by_measurement: dict[str, int] = {}
    by_measurement_metric: dict[str, int] = {}
    for point in points:
        by_measurement[point.measurement] = by_measurement.get(point.measurement, 0) + 1
        metric_key_value = f"{point.measurement}:{point.tags.get('metric', '-')}"
        by_measurement_metric[metric_key_value] = by_measurement_metric.get(metric_key_value, 0) + 1

    return {
        "dry_run": dry_run,
        "points": len(points),
        "written": written,
        "measurements": dict(sorted(by_measurement.items())),
        "metric_count": len(by_measurement_metric),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export LEAN backtest statistics and Monte Carlo reports to InfluxDB for Grafana Stat/Gauge panels.")
    parser.add_argument("--summary-file", default=str(default_summary_file()))
    parser.add_argument("--monte-carlo-file", action="append", dest="monte_carlo_files")
    parser.add_argument("--algorithm-id", default=DEFAULT_ALGORITHM_ID)
    parser.add_argument("--mode", default=DEFAULT_MODE)
    parser.add_argument("--run-id")
    parser.add_argument("--influx-url", default=DEFAULT_INFLUX_URL)
    parser.add_argument("--org", default=DEFAULT_INFLUX_ORG)
    parser.add_argument("--bucket", default=DEFAULT_INFLUX_BUCKET)
    parser.add_argument("--token", default=DEFAULT_INFLUX_TOKEN)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-lines", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    monte_carlo_files = args.monte_carlo_files or [str(path) for path in default_monte_carlo_files()]
    points = collect_points(
        summary_file=args.summary_file,
        monte_carlo_files=monte_carlo_files,
        algorithm_id=args.algorithm_id,
        run_id=args.run_id,
        mode=args.mode,
    )
    lines = [point_to_line_protocol(point) for point in points]

    if args.print_lines:
        for line in lines:
            print(line)

    written = 0
    if not args.dry_run:
        written = write_lines_to_influx(
            lines,
            influx_url=args.influx_url,
            org=args.org,
            bucket=args.bucket,
            token=resolve_influx_token(args.token),
        )

    print(json.dumps(build_summary(points, dry_run=args.dry_run, written=written), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
