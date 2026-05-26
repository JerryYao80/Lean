import json
import re
import zipfile
from pathlib import Path

import pandas as pd


PATH_KEYS = {
    "registry-file",
    "tushare-data-path",
    "lean-data-path",
    "report-file",
}
MONEY_MARKET_TICKERS = {"159001", "159003", "159005"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    return {
        "registry-file": str(root / "Common" / "Securities" / "Equity" / "AShareETFMetadata.cs"),
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
        "lean-data-path": str(root / "Data"),
        "start-date": "20180101",
        "end-date": "20251231",
        "exclude-money-market-etfs": True,
        "report-file": str(root / "Results" / "ashare-etf-export-report.json"),
        "dry-run": False,
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
        config_path = Path(config_path).resolve()
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        config.update(resolve_config_paths(loaded, config_path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    return resolve_config_paths(config, repo_root())


def is_money_market_ticker(ticker: str) -> bool:
    return ticker.startswith("511") or ticker in MONEY_MARKET_TICKERS


def market_to_suffix(market: str) -> str:
    return "SH" if market == "SSE" else "SZ"


def ts_code_to_lean_parts(ts_code: str) -> tuple[str, str]:
    ticker, suffix = ts_code.split(".")
    return ticker, "sse" if suffix == "SH" else "szse"


def load_registry_universe(registry_file: str | Path, exclude_money_market: bool = True) -> list[str]:
    text = Path(registry_file).read_text(encoding="utf-8")
    pattern = re.compile(r'\{\s*"(?P<ticker>\d{6})"\s*,\s*new AShareETFMetadata\s*\{(?P<body>.*?)\}\s*\},', re.S)

    symbols = []
    for match in pattern.finditer(text):
        body = match.group("body")
        if "TradingMode = ETFTradingMode.T0" not in body:
            continue

        ticker = match.group("ticker")
        if exclude_money_market and is_money_market_ticker(ticker):
            continue

        market_match = re.search(r'Market\s*=\s*"(?P<market>SSE|SZSE)"', body)
        if market_match is None:
            continue

        market = market_match.group("market")
        symbols.append(f"{ticker}.{market_to_suffix(market)}")

    return sorted(symbols)


def scale_price(value) -> int:
    return int(round(float(value) * 10000))


def scale_volume(value) -> int:
    return int(round(float(value) * 100))


def build_export_rows(frame: pd.DataFrame, start_date: str | None = None, end_date: str | None = None) -> list[str]:
    data = frame.copy()
    data["trade_date"] = data["trade_date"].astype(str).str.zfill(8)

    if start_date:
        data = data[data["trade_date"] >= start_date]
    if end_date:
        data = data[data["trade_date"] <= end_date]

    if data.empty:
        return []

    data = data.sort_values("trade_date")
    rows = []
    for _, row in data.iterrows():
        rows.append(
            f"{row['trade_date']} 00:00,{scale_price(row['open'])},{scale_price(row['high'])},{scale_price(row['low'])},{scale_price(row['close'])},{scale_volume(row['vol'])}"
        )
    return rows


def parquet_path(tushare_data_path: str | Path, ts_code: str) -> Path:
    return Path(tushare_data_path) / "fund_daily" / f"ts_code={ts_code}" / "data.parquet"


def lean_daily_paths(lean_data_path: str | Path, ts_code: str) -> tuple[Path, Path]:
    ticker, market = ts_code_to_lean_parts(ts_code)
    directory = Path(lean_data_path) / "equity" / market / "daily"
    return directory / f"{ticker}.csv", directory / f"{ticker}.zip"


def lean_auxiliary_paths(lean_data_path: str | Path, ts_code: str) -> tuple[Path, Path]:
    ticker, market = ts_code_to_lean_parts(ts_code)
    root = Path(lean_data_path) / "equity" / market
    return root / "map_files" / f"{ticker}.csv", root / "factor_files" / f"{ticker}.csv"


def load_rows_from_parquet(data_file: Path, start_date: str | None, end_date: str | None) -> list[str]:
    if not data_file.exists():
        return []
    frame = pd.read_parquet(data_file)
    return build_export_rows(frame, start_date=start_date, end_date=end_date)


def build_map_file_rows(ts_code: str) -> list[str]:
    ticker, market = ts_code_to_lean_parts(ts_code)
    return [f"19980101,{ticker},{ticker},{market}"]


def build_factor_file_rows() -> list[str]:
    return ["19980101,1.0,0.0"]


def write_auxiliary_files(lean_data_path: str | Path, ts_code: str) -> None:
    map_path, factor_path = lean_auxiliary_paths(lean_data_path, ts_code)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    factor_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text("\n".join(build_map_file_rows(ts_code)) + "\n", encoding="utf-8")
    factor_path.write_text("\n".join(build_factor_file_rows()) + "\n", encoding="utf-8")


def has_complete_lean_export(lean_data_path: str | Path, ts_code: str) -> bool:
    _, zip_path = lean_daily_paths(lean_data_path, ts_code)
    map_path, factor_path = lean_auxiliary_paths(lean_data_path, ts_code)
    return zip_path.exists() and map_path.exists() and factor_path.exists()


def collect_symbol_coverage_report(
    universe: list[str],
    tushare_data_path: str | Path,
    lean_data_path: str | Path,
    start_date: str | None,
    end_date: str | None,
) -> dict:
    missing_parquet = []
    missing_lean_export = []
    parquet_available = 0
    lean_export_count = 0

    for ts_code in sorted({str(symbol) for symbol in universe}):
        rows = load_rows_from_parquet(parquet_path(tushare_data_path, ts_code), start_date, end_date)
        if rows:
            parquet_available += 1
        else:
            missing_parquet.append(ts_code)

        if has_complete_lean_export(lean_data_path, ts_code):
            lean_export_count += 1
        elif rows:
            missing_lean_export.append(ts_code)

    return {
        "symbol_count": len(sorted({str(symbol) for symbol in universe})),
        "parquet_available_count": parquet_available,
        "lean_export_count": lean_export_count,
        "missing_parquet_symbols": missing_parquet,
        "missing_lean_export_symbols": missing_lean_export,
        "start_date": start_date,
        "end_date": end_date,
    }


def collect_coverage_report(
    registry_file: str | Path,
    tushare_data_path: str | Path,
    lean_data_path: str | Path,
    start_date: str | None,
    end_date: str | None,
    exclude_money_market: bool = True,
) -> dict:
    universe = load_registry_universe(registry_file, exclude_money_market=exclude_money_market)
    report = collect_symbol_coverage_report(
        universe=universe,
        tushare_data_path=tushare_data_path,
        lean_data_path=lean_data_path,
        start_date=start_date,
        end_date=end_date,
    )
    report["registry_symbol_count"] = report.pop("symbol_count")
    report["exclude_money_market_etfs"] = exclude_money_market
    return report


def validate_zip_rows(zip_path: Path, ticker: str, expected_rows: list[str]) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        with archive.open(f"{ticker.lower()}.csv") as handle:
            actual_rows = handle.read().decode("utf-8").strip().splitlines()

    if actual_rows != expected_rows:
        raise ValueError(f"Zip validation failed for {zip_path}")


def export_symbol(ts_code: str, tushare_data_path: str | Path, lean_data_path: str | Path, start_date: str | None, end_date: str | None) -> bool:
    rows = load_rows_from_parquet(parquet_path(tushare_data_path, ts_code), start_date, end_date)
    if not rows:
        return False

    csv_path, zip_path = lean_daily_paths(lean_data_path, ts_code)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    ticker, _ = ts_code_to_lean_parts(ts_code)

    csv_path.write_text("Date,Open,High,Low,Close,Volume\n" + "\n".join(rows), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{ticker.lower()}.csv", "\n".join(rows))

    validate_zip_rows(zip_path, ticker, rows)
    write_auxiliary_files(lean_data_path, ts_code)
    return True


def export_symbol_universe(
    universe: list[str],
    tushare_data_path: str | Path,
    lean_data_path: str | Path,
    start_date: str | None,
    end_date: str | None,
    report_file: str | Path | None = None,
) -> dict:
    exported_symbols = []
    for ts_code in sorted({str(symbol) for symbol in universe}):
        if export_symbol(ts_code, tushare_data_path, lean_data_path, start_date, end_date):
            exported_symbols.append(ts_code)

    report = collect_symbol_coverage_report(
        universe=universe,
        tushare_data_path=tushare_data_path,
        lean_data_path=lean_data_path,
        start_date=start_date,
        end_date=end_date,
    )
    report["exported_symbols"] = exported_symbols
    report["exported_count"] = len(exported_symbols)

    if report_file:
        path = Path(report_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def export_registry_universe(config: dict) -> dict:
    universe = load_registry_universe(
        config["registry-file"],
        exclude_money_market=config.get("exclude-money-market-etfs", True),
    )
    report = export_symbol_universe(
        universe=universe,
        tushare_data_path=config["tushare-data-path"],
        lean_data_path=config["lean-data-path"],
        start_date=config.get("start-date"),
        end_date=config.get("end-date"),
        report_file=config.get("report-file"),
    )
    report["registry_symbol_count"] = report.pop("symbol_count")
    report["exclude_money_market_etfs"] = config.get("exclude-money-market-etfs", True)
    return report
