from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    paths: tuple[str, ...]
    date_field: str | None = None
    symbol_field: str | None = None
    announcement_fields: tuple[str, ...] = ()


DEFAULT_INCLUDED_MARKETS = ("主板", "创业板", "科创板")


class BarraCNE5DataLoader:
    def __init__(self, data_root: str | Path, catalog: str | Path | dict | None = None):
        self.data_root = Path(data_root)
        self.catalog = self._load_catalog(catalog)
        self._cache: dict[tuple[str, str | None], pd.DataFrame] = {}

    def load_dataset(
        self,
        dataset: str,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        spec = self.catalog[dataset]
        frame = self._read_dataset(spec, symbol=symbol)
        if frame.empty:
            return frame

        data = frame.copy()
        if spec.symbol_field and symbol and spec.symbol_field in data.columns:
            data = data[data[spec.symbol_field].astype(str) == str(symbol)]

        if spec.date_field and spec.date_field in data.columns:
            data[spec.date_field] = data[spec.date_field].astype(str).str.zfill(8)
            if start_date:
                data = data[data[spec.date_field] >= start_date]
            if end_date:
                data = data[data[spec.date_field] <= end_date]
            data = data.sort_values(spec.date_field)

        for field in spec.announcement_fields:
            if field in data.columns:
                data[field] = data[field].astype(str).str.zfill(8)

        if fields is not None:
            keep = []
            for candidate in [spec.date_field, spec.symbol_field, *spec.announcement_fields]:
                if candidate and candidate in data.columns and candidate not in keep:
                    keep.append(candidate)
            for field in fields:
                if field in data.columns and field not in keep:
                    keep.append(field)
            data = data[keep]

        return self._to_object_frame(data.reset_index(drop=True))

    def load_point_in_time(
        self,
        dataset: str,
        symbol: str | None,
        asof_date: str,
        fields: list[str] | None = None,
    ) -> pd.Series | None:
        spec = self.catalog[dataset]
        frame = self.load_dataset(dataset, symbol=symbol, fields=fields)
        if frame.empty:
            return None

        data = frame.copy()
        sort_fields: list[str] = []
        for field in spec.announcement_fields:
            if field in data.columns:
                data = data[data[field].astype(str) <= asof_date]
                sort_fields.append(field)

        if spec.date_field and spec.date_field in data.columns:
            data = data[data[spec.date_field].astype(str) <= asof_date]
            sort_fields.append(spec.date_field)

        if data.empty:
            return None

        data = data.sort_values(sort_fields or [spec.date_field] if spec.date_field else data.columns.tolist())
        return data.iloc[-1]

    def load_stock_universe(
        self,
        asof_date: str,
        universe: str | list[str] | tuple[str, ...] = "all-a",
        index_code: str = "000300.SH",
        included_markets: tuple[str, ...] | list[str] | None = None,
        list_status: str = "L",
    ) -> list[str]:
        if isinstance(universe, (list, tuple)):
            return sorted({str(symbol) for symbol in universe})

        token = (universe or "all-a").strip().lower()
        if token == "csi300":
            members = self.load_index_constituents(asof_date=asof_date, index_code=index_code)
            if members:
                return members

        frame = self.load_dataset("stock_basic")
        if frame.empty or "ts_code" not in frame.columns:
            return []

        data = frame.copy()
        if "list_status" in data.columns:
            data = data[data["list_status"].astype(str) == list_status]

        markets = tuple(included_markets or DEFAULT_INCLUDED_MARKETS)
        if "market" in data.columns:
            data = data[data["market"].isin(markets)]

        if "list_date" in data.columns:
            data = data[data["list_date"].astype(str) <= asof_date]

        return sorted(data["ts_code"].dropna().astype(str).unique().tolist())

    def load_index_constituents(self, asof_date: str, index_code: str = "000300.SH") -> list[str]:
        if "index_weight" not in self.catalog:
            return []

        frame = self.load_dataset("index_weight", end_date=asof_date)
        if frame.empty:
            return []

        data = frame.copy()
        if "index_code" in data.columns:
            data = data[data["index_code"].astype(str) == index_code]
        if data.empty or "trade_date" not in data.columns or "con_code" not in data.columns:
            return []

        latest_trade_date = data["trade_date"].astype(str).max()
        data = data[data["trade_date"].astype(str) == latest_trade_date]
        return sorted(data["con_code"].dropna().astype(str).unique().tolist())

    def get_trading_dates(
        self,
        start_date: str,
        end_date: str,
        reference_symbol: str = "000300.SH",
    ) -> list[str]:
        if "trade_cal" in self.catalog:
            frame = self.load_dataset("trade_cal", start_date=start_date, end_date=end_date)
            if not frame.empty and "cal_date" in frame.columns:
                data = frame.copy()
                if "is_open" in data.columns:
                    data = data[data["is_open"].astype(str).isin({"1", "True", "true"})]
                return sorted(data["cal_date"].astype(str).unique().tolist())

        frame = self.load_dataset("index_daily", symbol=reference_symbol, start_date=start_date, end_date=end_date)
        if frame.empty or "trade_date" not in frame.columns:
            return []
        return sorted(frame["trade_date"].astype(str).unique().tolist())

    def _load_catalog(self, catalog: str | Path | dict | None) -> dict[str, DatasetSpec]:
        if catalog is None:
            raw = self._default_catalog()
        elif isinstance(catalog, dict):
            raw = catalog
        else:
            raw = json.loads(Path(catalog).read_text(encoding="utf-8"))

        return {
            name: DatasetSpec(
                name=name,
                paths=tuple(definition["paths"] if "paths" in definition else [definition["path"]]),
                date_field=definition.get("date_field"),
                symbol_field=definition.get("symbol_field"),
                announcement_fields=tuple(definition.get("announcement_fields", [])),
            )
            for name, definition in raw["datasets"].items()
        }

    def _default_catalog(self) -> dict:
        return {
            "datasets": {
                "daily": {
                    "path": "daily/ts_code={symbol}/data.parquet",
                    "date_field": "trade_date",
                    "symbol_field": "ts_code",
                },
                "daily_basic": {
                    "path": "daily_basic/ts_code={symbol}/data.parquet",
                    "date_field": "trade_date",
                    "symbol_field": "ts_code",
                },
                "income": {
                    "path": "income/ts_code={symbol}/data.parquet",
                    "date_field": "end_date",
                    "symbol_field": "ts_code",
                    "announcement_fields": ["f_ann_date", "ann_date"],
                },
                "balancesheet": {
                    "path": "balancesheet/ts_code={symbol}/data.parquet",
                    "date_field": "end_date",
                    "symbol_field": "ts_code",
                    "announcement_fields": ["f_ann_date", "ann_date"],
                },
                "cashflow": {
                    "path": "cashflow/ts_code={symbol}/data.parquet",
                    "date_field": "end_date",
                    "symbol_field": "ts_code",
                    "announcement_fields": ["f_ann_date", "ann_date"],
                },
                "stock_basic": {
                    "path": "stock_basic/data.parquet",
                    "symbol_field": "ts_code",
                },
                "index_daily": {
                    "path": "index_daily/ts_code={symbol}/data.parquet",
                    "date_field": "trade_date",
                    "symbol_field": "ts_code",
                },
                "index_weight": {
                    "paths": [
                        "index_weight/date=*/data.parquet",
                        "index_weight/year=*/data.parquet",
                        "index_weight/data.parquet",
                    ],
                    "date_field": "trade_date",
                    "symbol_field": "index_code",
                },
                "shibor": {
                    "paths": [
                        "shibor/year=*/data.parquet",
                        "shibor/data.parquet",
                    ],
                    "date_field": "date",
                },
                "trade_cal": {
                    "path": "trade_cal/data.parquet",
                    "date_field": "cal_date",
                },
            }
        }

    def _read_dataset(self, spec: DatasetSpec, symbol: str | None = None) -> pd.DataFrame:
        cache_key = (spec.name, symbol)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached.copy()

        files: list[Path] = []
        seen: set[Path] = set()
        for pattern in spec.paths:
            for path in self._discover_files(pattern, symbol):
                if path not in seen:
                    seen.add(path)
                    files.append(path)

        if not files:
            frame = pd.DataFrame()
            self._cache[cache_key] = frame
            return frame.copy()

        frames = [pd.read_parquet(path) for path in files]
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        self._cache[cache_key] = frame
        return frame.copy()

    def _discover_files(self, pattern: str, symbol: str | None) -> list[Path]:
        if "{symbol}" in pattern:
            if not symbol:
                return sorted(self.data_root.glob(pattern.replace("{symbol}", "*")))
            path = self.data_root / pattern.format(symbol=symbol)
            return [path] if path.exists() else []

        if "*" in pattern:
            return sorted(self.data_root.glob(pattern))

        path = self.data_root / pattern
        return [path] if path.exists() else []

    @staticmethod
    def _to_object_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        return frame.astype(object).where(pd.notna(frame), None)
