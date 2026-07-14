from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

import pandas as pd


_HASH_BLOCK_SIZE = 1024 * 1024
_SUPPORTED_SUFFIXES = {".csv", ".parquet"}
_518880_MARKER = re.compile(r"(?:^|[^0-9])518880(?:[^0-9]|$)")
_PROHIBITED_DECLARATION = re.compile(r"(?:^|[^a-z])(proxy|prelisting)(?:[^a-z]|$)")


@dataclass(frozen=True)
class SourceReport:
    logical_name: str
    path: str
    first_date: str
    last_date: str
    row_count: int
    distinct_date_count: int
    duplicate_date_count: int
    sha256: str
    annual_counts: Mapping[int, int]


def inspect_source(logical_name: str, path: Path, date_column: str) -> SourceReport:
    source_path = Path(path)
    if logical_name == "fund_nav":
        raise ValueError("fund_nav cannot substitute for a tradable source")
    if not source_path.is_file():
        raise ValueError(f"source file does not exist: {source_path}")

    suffix = source_path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported source suffix: {suffix}")

    is_518880 = bool(_518880_MARKER.search(logical_name))
    declaration = f"{logical_name} {source_path}".lower()
    prohibited = _PROHIBITED_DECLARATION.search(declaration)
    if is_518880 and prohibited:
        raise ValueError(f"518880 source declares prohibited {prohibited.group(1)} input")

    frame = pd.read_parquet(source_path) if suffix == ".parquet" else pd.read_csv(source_path)
    if frame.empty:
        raise ValueError("source is empty")
    if date_column not in frame.columns:
        raise ValueError(f"date column does not exist: {date_column}")
    if is_518880 and not {"open", "high", "low", "close"}.issubset(frame.columns):
        raise ValueError("518880 tradable source requires OHLC columns")

    dates = [_parse_date(value) for value in frame[date_column]]
    distinct_dates = set(dates)
    annual_counts: dict[int, int] = {}
    for value in dates:
        annual_counts[value.year] = annual_counts.get(value.year, 0) + 1

    return SourceReport(
        logical_name=logical_name,
        path=str(source_path.resolve()),
        first_date=min(dates).isoformat(),
        last_date=max(dates).isoformat(),
        row_count=len(dates),
        distinct_date_count=len(distinct_dates),
        duplicate_date_count=len(dates) - len(distinct_dates),
        sha256=_sha256(source_path),
        annual_counts=MappingProxyType(dict(sorted(annual_counts.items()))),
    )


def _parse_date(value: object) -> date:
    if pd.isna(value):
        raise ValueError("date column contains null or unparseable values")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{8}", text):
            return datetime.strptime(text, "%Y%m%d").date()
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (TypeError, ValueError) as error:
        raise ValueError(f"date column contains null or unparseable value: {value!r}") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(_HASH_BLOCK_SIZE):
            digest.update(block)
    return digest.hexdigest()
