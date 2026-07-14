from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
from io import BytesIO
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

import pandas as pd


_HASH_BLOCK_SIZE = 1024 * 1024
_SUPPORTED_SUFFIXES = {".csv", ".parquet"}
_PROHIBITED_DECLARATION = re.compile(r"(?:^|[^a-z])(proxy|prelisting)(?:[^a-z]|$)")


class SourceKind(StrEnum):
    TRADABLE = "tradable"
    BENCHMARK = "benchmark"
    FUND_NAV = "fund_nav"


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


def inspect_source(
    logical_name: str,
    path: Path,
    date_column: str,
    *,
    source_kind: SourceKind,
    instrument: str,
) -> SourceReport:
    if not isinstance(source_kind, SourceKind):
        raise ValueError("source kind must be a canonical SourceKind")
    if source_kind is SourceKind.FUND_NAV:
        raise ValueError("fund_nav cannot substitute for a tradable source")
    if source_kind is SourceKind.TRADABLE and instrument != "518880":
        raise ValueError("unknown tradable instrument; expected canonical 518880")
    if source_kind is SourceKind.BENCHMARK and instrument != "CSI300":
        raise ValueError("unknown benchmark instrument; expected canonical CSI300")

    requested_path = Path(path)
    try:
        resolved_path = requested_path.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"source file does not exist: {requested_path}") from error
    if not resolved_path.is_file():
        raise ValueError(f"source file does not exist: {requested_path}")

    suffix = resolved_path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported source suffix: {suffix}")

    if source_kind is SourceKind.TRADABLE:
        prohibited = _PROHIBITED_DECLARATION.search(
            f"{logical_name} {requested_path} {resolved_path}".lower()
        )
        if prohibited:
            raise ValueError(
                f"518880 source declares prohibited {prohibited.group(1)} input"
            )

    before = resolved_path.stat()
    content = _read_bytes(resolved_path)
    frame = _read_frame(BytesIO(content), suffix)
    digest = hashlib.sha256(content).hexdigest()
    after_parse = resolved_path.stat()
    if _file_identity(before) != _file_identity(after_parse):
        raise ValueError("source file changed during inspection")
    if _sha256(resolved_path) != digest:
        raise ValueError("source file changed during inspection")
    try:
        after_resolved = requested_path.resolve(strict=True)
        after = after_resolved.stat()
    except FileNotFoundError as error:
        raise ValueError("source file changed during inspection") from error
    if after_resolved != resolved_path or _file_identity(before) != _file_identity(after):
        raise ValueError("source file changed during inspection")

    if frame.empty:
        raise ValueError("source is empty")
    if date_column not in frame.columns:
        raise ValueError(f"date column does not exist: {date_column}")
    if source_kind is SourceKind.TRADABLE and not {
        "open",
        "high",
        "low",
        "close",
    }.issubset(frame.columns):
        raise ValueError("518880 tradable source requires OHLC columns")

    dates = [_parse_date(value) for value in frame[date_column]]
    distinct_dates = set(dates)
    annual_counts: dict[int, int] = {}
    for value in dates:
        annual_counts[value.year] = annual_counts.get(value.year, 0) + 1

    return SourceReport(
        logical_name=logical_name,
        path=str(resolved_path),
        first_date=min(dates).date().isoformat(),
        last_date=max(dates).date().isoformat(),
        row_count=len(dates),
        distinct_date_count=len(distinct_dates),
        duplicate_date_count=len(dates) - len(distinct_dates),
        sha256=digest,
        annual_counts=MappingProxyType(dict(sorted(annual_counts.items()))),
    )


def _read_frame(source: object, suffix: str) -> pd.DataFrame:
    return pd.read_parquet(source) if suffix == ".parquet" else pd.read_csv(source)


def _read_bytes(path: Path) -> bytes:
    chunks: list[bytes] = []
    with path.open("rb") as source:
        while block := source.read(_HASH_BLOCK_SIZE):
            chunks.append(block)
    return b"".join(chunks)


def _parse_date(value: object) -> datetime:
    if pd.isna(value):
        raise ValueError("date column contains null or unparseable values")
    text = str(value)
    try:
        if re.fullmatch(r"\d{8}", text):
            return datetime.strptime(text, "%Y%m%d")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return datetime.strptime(text, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError(f"date column contains unparseable value: {value!r}") from error
    raise ValueError(f"date column contains unsupported date format: {value!r}")


def _file_identity(stat_result: object) -> tuple[int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(_HASH_BLOCK_SIZE):
            digest.update(block)
    return digest.hexdigest()
