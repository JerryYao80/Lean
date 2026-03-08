import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    path: str
    date_field: str | None = None
    symbol_field: str | None = None


class TushareDataLayer:
    def __init__(self, data_root: str | Path, catalog: str | Path | dict | None = None):
        self.data_root = Path(data_root)
        self.catalog = self._load_catalog(catalog)
        self._raw_cache: dict[tuple[str, tuple[str, ...]], pd.DataFrame] = {}

    def list_datasets(self) -> list[str]:
        return sorted(self.catalog.keys())

    def list_fields(self, dataset: str) -> list[str]:
        spec = self.catalog[dataset]
        files = self._discover_files(spec)
        if not files:
            return []
        frame = pd.read_parquet(files[0])
        return frame.columns.tolist()

    def load_dataset(
        self,
        dataset: str,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        spec = self.catalog[dataset]
        frame = self._read_dataset(spec, symbol=symbol, start_date=start_date, end_date=end_date)
        if frame.empty:
            return frame

        if spec.symbol_field and symbol and spec.symbol_field in frame.columns:
            frame = frame[frame[spec.symbol_field] == symbol]

        if spec.date_field and spec.date_field in frame.columns:
            frame = frame.copy()
            frame[spec.date_field] = frame[spec.date_field].astype(str).str.zfill(8)
            if start_date:
                frame = frame[frame[spec.date_field] >= start_date]
            if end_date:
                frame = frame[frame[spec.date_field] <= end_date]
            frame = frame.sort_values(spec.date_field)

        if fields is not None:
            keep = []
            if spec.date_field and spec.date_field in frame.columns:
                keep.append(spec.date_field)
            if spec.symbol_field and spec.symbol_field in frame.columns and spec.symbol_field not in keep:
                keep.append(spec.symbol_field)
            for field in fields:
                if field in frame.columns and field not in keep:
                    keep.append(field)
            frame = frame[keep]

        return self._to_object_frame(frame.reset_index(drop=True))

    def build_feature_frame(
        self,
        symbol: str,
        time_series: list[dict] | None = None,
        static: list[dict] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        date_alias: str = 'date',
    ) -> pd.DataFrame:
        feature_frame = None

        for request in time_series or []:
            dataset = request['dataset']
            frame = self.load_dataset(
                dataset,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                fields=request.get('fields'),
            )
            if frame.empty:
                continue

            spec = self.catalog[dataset]
            if spec.date_field and spec.date_field in frame.columns:
                frame = frame.rename(columns={spec.date_field: date_alias})

            if feature_frame is None:
                feature_frame = frame
            else:
                feature_frame = feature_frame.merge(frame, on=date_alias, how='outer')

        if feature_frame is None:
            feature_frame = pd.DataFrame(columns=[date_alias])

        if date_alias in feature_frame.columns:
            feature_frame[date_alias] = feature_frame[date_alias].astype(str)
            feature_frame = feature_frame.sort_values(date_alias).reset_index(drop=True)

        for request in static or []:
            dataset = request['dataset']
            frame = self.load_dataset(dataset, symbol=symbol, fields=request.get('fields'))
            if frame.empty:
                continue

            spec = self.catalog[dataset]
            if spec.date_field and spec.date_field in frame.columns:
                frame = frame.drop(columns=[spec.date_field])

            frame = frame.drop_duplicates().head(1)
            values = frame.iloc[0].to_dict()

            if feature_frame.empty:
                feature_frame = pd.DataFrame([values])
            else:
                for key, value in values.items():
                    feature_frame[key] = value

        return self._to_object_frame(feature_frame.reset_index(drop=True))

    def _default_catalog_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / 'Launcher' / 'config' / 'config-ashare-dataset-catalog.json'

    def _load_catalog(self, catalog: str | Path | dict | None) -> dict[str, DatasetSpec]:
        source = catalog or self._default_catalog_path()
        if isinstance(source, dict):
            raw = source
        else:
            raw = json.loads(Path(source).read_text(encoding='utf-8'))

        return {
            name: DatasetSpec(name=name, **definition)
            for name, definition in raw['datasets'].items()
        }

    def _discover_files(
        self,
        spec: DatasetSpec,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[Path]:
        if '{symbol}' in spec.path:
            if symbol:
                path = self.data_root / spec.path.format(symbol=symbol)
                return [path] if path.exists() else []
            return sorted(self.data_root.glob(spec.path.replace('{symbol}', '*')))

        if '*' in spec.path:
            files = sorted(self.data_root.glob(spec.path))
            return self._filter_partitioned_files(files, start_date=start_date, end_date=end_date)

        path = self.data_root / spec.path
        return [path] if path.exists() else []

    def _read_dataset(
        self,
        spec: DatasetSpec,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        files = self._discover_files(spec, symbol=symbol, start_date=start_date, end_date=end_date)
        if not files:
            return pd.DataFrame()

        cache_key = (spec.name, tuple(str(path) for path in files))
        cached = self._raw_cache.get(cache_key)
        if cached is not None:
            return cached.copy()

        frames = [pd.read_parquet(path) for path in files]
        if not frames:
            return pd.DataFrame()

        frame = pd.concat(frames, ignore_index=True)
        self._raw_cache[cache_key] = frame
        return frame.copy()

    @staticmethod
    def _filter_partitioned_files(files: list[Path], start_date: str | None, end_date: str | None) -> list[Path]:
        if not files:
            return files

        start_year = int(start_date[:4]) if start_date and len(start_date) >= 4 else None
        end_year = int(end_date[:4]) if end_date and len(end_date) >= 4 else None
        filtered = []

        for path in files:
            partition_values = {}
            for part in path.parts:
                if '=' not in part:
                    continue
                key, value = part.split('=', 1)
                partition_values[key] = value

            year_value = partition_values.get('year')
            if year_value and year_value.isdigit():
                year = int(year_value)
                if start_year is not None and year < start_year:
                    continue
                if end_year is not None and year > end_year:
                    continue

            date_value = partition_values.get('date')
            if date_value and len(date_value) == 8 and date_value.isdigit():
                if start_date and date_value < start_date:
                    continue
                if end_date and date_value > end_date:
                    continue

            filtered.append(path)

        return filtered

    @staticmethod
    def _to_object_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        return frame.astype(object).where(pd.notna(frame), None)
