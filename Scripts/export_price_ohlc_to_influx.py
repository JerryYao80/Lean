from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request
from zoneinfo import ZoneInfo

import pandas as pd


CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_MEASUREMENT = "lean_price_ohlc"
DEFAULT_TUSHARE_DATA_PATH = "/home/project/tushare-downloader/tushare_data_v2"
DEFAULT_ARCHIVE_PATH = str(Path(__file__).resolve().parents[1] / "Data" / "archive" / "barra-cne5-live-daily-quotes")
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")


@dataclass(frozen=True)
class PriceOhlcRecord:
    symbol: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str
    source_api: str | None = None
    amount: float | None = None
    pre_close: float | None = None
    pct_chg: float | None = None

    @property
    def timestamp_ns(self) -> int:
        return trade_date_to_timestamp_ns(self.trade_date)


def normalize_trade_date(value) -> str:
    if value is None or pd.isna(value):
        return ""

    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y%m%d")

    text = str(value).strip()
    if not text:
        return ""

    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    if re.fullmatch(r"\d{8}", text):
        return text

    parsed = pd.to_datetime(text, errors="coerce")
    if not pd.isna(parsed):
        return parsed.strftime("%Y%m%d")

    digits = re.sub(r"\D", "", text)
    return digits[:8] if len(digits) >= 8 else ""


def trade_date_to_timestamp_ns(trade_date: str) -> int:
    normalized = normalize_trade_date(trade_date)
    if not re.fullmatch(r"\d{8}", normalized):
        raise ValueError(f"Invalid trade_date: {trade_date!r}")

    local_close = datetime(
        int(normalized[0:4]),
        int(normalized[4:6]),
        int(normalized[6:8]),
        15,
        0,
        0,
        tzinfo=CHINA_TZ,
    )
    utc_close = local_close.astimezone(timezone.utc)
    return int(utc_close.timestamp() * 1_000_000_000)


def clean_text(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def to_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def date_in_range(trade_date: str, start_date: str | None, end_date: str | None) -> bool:
    return bool(trade_date) and (start_date is None or trade_date >= start_date) and (end_date is None or trade_date <= end_date)


def parse_symbol_list(symbols: str | None, symbols_file: str | Path | None = None) -> list[str] | None:
    parsed: list[str] = []
    if symbols:
        parsed.extend(part.strip() for part in re.split(r"[,\s]+", symbols) if part.strip())

    if symbols_file:
        for line in Path(symbols_file).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                parsed.append(stripped)

    if not parsed:
        return None

    unique: list[str] = []
    seen = set()
    for symbol in parsed:
        if symbol not in seen:
            unique.append(symbol)
            seen.add(symbol)
    return unique


def row_to_record(row, source: str, default_symbol: str | None = None) -> PriceOhlcRecord | None:
    symbol = clean_text(row.get("ts_code")) or default_symbol
    trade_date = normalize_trade_date(row.get("trade_date"))
    close = to_float(row.get("close", row.get("price")))
    if symbol is None or close is None or not date_in_range(trade_date, None, None):
        return None

    open_value = to_float(row.get("open"))
    high_value = to_float(row.get("high"))
    low_value = to_float(row.get("low"))
    volume_value = to_float(row.get("vol", row.get("volume")))

    open_value = close if open_value is None else open_value
    high_value = max(open_value, close) if high_value is None else high_value
    low_value = min(open_value, close) if low_value is None else low_value

    return PriceOhlcRecord(
        symbol=symbol,
        trade_date=trade_date,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close,
        volume=(volume_value or 0.0) * 100.0,
        source=source,
        source_api=clean_text(row.get("source_api")),
        amount=to_float(row.get("amount")),
        pre_close=to_float(row.get("pre_close")),
        pct_chg=to_float(row.get("pct_chg")),
    )


def tushare_daily_file(tushare_data_path: str | Path, symbol: str) -> Path:
    return Path(tushare_data_path) / "daily" / f"ts_code={symbol}" / "data.parquet"


def iter_tushare_daily_files(tushare_data_path: str | Path, symbols: list[str] | None, limit_symbols: int | None = None):
    if symbols:
        for symbol in symbols[:limit_symbols]:
            yield symbol, tushare_daily_file(tushare_data_path, symbol)
        return

    daily_root = Path(tushare_data_path) / "daily"
    yielded = 0
    for data_file in sorted(daily_root.glob("ts_code=*/data.parquet")):
        symbol = data_file.parent.name.removeprefix("ts_code=")
        yield symbol, data_file
        yielded += 1
        if limit_symbols is not None and yielded >= limit_symbols:
            break


def load_tushare_daily_records(
    tushare_data_path: str | Path,
    symbols: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit_symbols: int | None = None,
) -> list[PriceOhlcRecord]:
    records: list[PriceOhlcRecord] = []
    for symbol, data_file in iter_tushare_daily_files(tushare_data_path, symbols, limit_symbols=limit_symbols):
        if not data_file.exists():
            continue

        frame = pd.read_parquet(data_file)
        if frame.empty:
            continue

        data = frame.copy()
        if "ts_code" not in data.columns:
            data["ts_code"] = symbol
        data["trade_date"] = data["trade_date"].map(normalize_trade_date)
        data = data[data["trade_date"].map(lambda value: date_in_range(value, start_date, end_date))]
        if data.empty:
            continue

        data = data.sort_values("trade_date").drop_duplicates(["ts_code", "trade_date"], keep="last")
        for _, row in data.iterrows():
            record = row_to_record(row, source="tushare_daily", default_symbol=symbol)
            if record is not None:
                records.append(record)

    return sorted(records, key=lambda item: (item.symbol, item.trade_date))


def archive_date_from_path(path: Path) -> str:
    return normalize_trade_date(path.parent.name.removeprefix("date="))


def load_live_archive_records(
    archive_path: str | Path,
    symbols: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit_symbols: int | None = None,
) -> list[PriceOhlcRecord]:
    symbol_filter = set(symbols) if symbols else None
    records: list[PriceOhlcRecord] = []

    for data_file in sorted(Path(archive_path).glob("date=*/quotes.parquet")):
        archive_date = archive_date_from_path(data_file)
        if archive_date and not date_in_range(archive_date, start_date, end_date):
            continue

        frame = pd.read_parquet(data_file)
        if frame.empty or "ts_code" not in frame.columns:
            continue

        data = frame.copy()
        if "trade_date" not in data.columns:
            data["trade_date"] = archive_date
        data["trade_date"] = data["trade_date"].map(normalize_trade_date)
        data = data[data["trade_date"].map(lambda value: date_in_range(value, start_date, end_date))]
        if symbol_filter is not None:
            data = data[data["ts_code"].isin(symbol_filter)]
        if data.empty:
            continue

        if "fetch_timestamp" in data.columns:
            data["_fetch_sort"] = pd.to_datetime(data["fetch_timestamp"], errors="coerce", utc=True)
            data = data.sort_values(["ts_code", "trade_date", "_fetch_sort"], na_position="first")
        else:
            data = data.reset_index().sort_values(["ts_code", "trade_date", "index"])

        data = data.drop_duplicates(["ts_code", "trade_date"], keep="last")
        for _, row in data.iterrows():
            record = row_to_record(row, source="live_archive")
            if record is not None:
                records.append(record)

    records = sorted(records, key=lambda item: (item.symbol, item.trade_date, item.source_api or ""))
    if limit_symbols is None or symbols:
        return records

    allowed = set(sorted({record.symbol for record in records})[:limit_symbols])
    return [record for record in records if record.symbol in allowed]


def collect_price_ohlc_records(
    source: str,
    tushare_data_path: str | Path,
    archive_path: str | Path,
    symbols: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit_symbols: int | None = None,
) -> list[PriceOhlcRecord]:
    records: list[PriceOhlcRecord] = []
    if source in {"all", "tushare"}:
        records.extend(
            load_tushare_daily_records(
                tushare_data_path=tushare_data_path,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                limit_symbols=limit_symbols,
            )
        )
    if source in {"all", "live_archive"}:
        records.extend(
            load_live_archive_records(
                archive_path=archive_path,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                limit_symbols=limit_symbols,
            )
        )

    return sorted(records, key=lambda item: (item.source, item.symbol, item.trade_date))


def escape_key(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def escape_tag_value(value: str) -> str:
    return escape_key(value)


def format_field_value(value) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    parsed = to_float(value)
    if parsed is None:
        raise ValueError(f"Invalid numeric field value: {value!r}")
    return format(parsed, ".12g")


def record_to_line_protocol(record: PriceOhlcRecord, measurement: str = DEFAULT_MEASUREMENT) -> str:
    tags = {
        "source": record.source,
        "symbol": record.symbol,
    }
    if record.source_api:
        tags["source_api"] = record.source_api

    fields = {
        "open": record.open,
        "high": record.high,
        "low": record.low,
        "close": record.close,
        "volume": record.volume,
    }
    if record.amount is not None:
        fields["amount"] = record.amount
    if record.pre_close is not None:
        fields["pre_close"] = record.pre_close
    if record.pct_chg is not None:
        fields["pct_chg"] = record.pct_chg

    tag_set = ",".join(f"{escape_key(key)}={escape_tag_value(str(value))}" for key, value in sorted(tags.items()))
    field_set = ",".join(f"{escape_key(key)}={format_field_value(value)}" for key, value in fields.items())
    return f"{escape_key(measurement)},{tag_set} {field_set} {record.timestamp_ns}"


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
    payload = ("\n".join(payload_lines) + "\n").encode("utf-8")
    influx_request = request.Request(
        url,
        data=payload,
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


def normalize_date_arg(parser: argparse.ArgumentParser, value: str | None, name: str) -> str | None:
    if value is None:
        return None
    normalized = normalize_trade_date(value)
    if not re.fullmatch(r"\d{8}", normalized):
        parser.error(f"{name} must be YYYYMMDD or YYYY-MM-DD, got {value!r}")
    return normalized


def build_summary(records: list[PriceOhlcRecord], measurement: str, dry_run: bool, written: int = 0) -> dict:
    source_counts = Counter(record.source for record in records)
    symbols = sorted({record.symbol for record in records})
    dates = sorted({record.trade_date for record in records})
    return {
        "measurement": measurement,
        "dry_run": dry_run,
        "records": len(records),
        "written": written,
        "sources": dict(sorted(source_counts.items())),
        "symbol_count": len(symbols),
        "symbols": symbols[:20],
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export A-share daily OHLC data to InfluxDB for Grafana candlestick panels.")
    parser.add_argument("--tushare-data-path", default=DEFAULT_TUSHARE_DATA_PATH)
    parser.add_argument("--archive-path", default=DEFAULT_ARCHIVE_PATH)
    parser.add_argument("--symbols", help="Comma or whitespace separated ts_code list, for example 000001.SZ,600000.SH.")
    parser.add_argument("--symbols-file", help="File containing one ts_code per line.")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--source", choices=["all", "tushare", "live_archive"], default="all")
    parser.add_argument("--limit-symbols", type=int)
    parser.add_argument("--measurement", default=DEFAULT_MEASUREMENT)
    parser.add_argument("--influx-url", default=DEFAULT_INFLUX_URL)
    parser.add_argument("--org", default=DEFAULT_INFLUX_ORG)
    parser.add_argument("--bucket", default=DEFAULT_INFLUX_BUCKET)
    parser.add_argument("--token", default=DEFAULT_INFLUX_TOKEN)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-lines", action="store_true", help="Print generated line protocol lines before the summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    start_date = normalize_date_arg(parser, args.start_date, "--start-date")
    end_date = normalize_date_arg(parser, args.end_date, "--end-date")
    if start_date and end_date and start_date > end_date:
        parser.error("--start-date must be earlier than or equal to --end-date")

    symbols = parse_symbol_list(args.symbols, args.symbols_file)
    records = collect_price_ohlc_records(
        source=args.source,
        tushare_data_path=args.tushare_data_path,
        archive_path=args.archive_path,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        limit_symbols=args.limit_symbols,
    )
    lines = [record_to_line_protocol(record, measurement=args.measurement) for record in records]

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

    print(json.dumps(build_summary(records, args.measurement, args.dry_run, written=written), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
