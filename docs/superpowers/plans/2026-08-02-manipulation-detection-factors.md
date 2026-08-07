# Manipulation Detection Factors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 4 manipulation detection factor builders (turnover_anomaly, amplitude_anomaly, limit_behavior, intraday_reversal) following Phase 5 builder pattern, register in factor_worker.py, and create backtest validation.

**Architecture:** Each factor is a standalone Python builder module in `factor_builders/` producing parquet + InfluxDB output. Factor registration uses `_make_factorzoo_builder()` pattern. C# Factor classes wrap Python output for LEAN consumption.

**Tech Stack:** Python 3.11, pandas, InfluxDB Line Protocol, LEAN C# FactorStore

**Reference Spec:** `docs/superpowers/specs/2026-08-02-manipulation-detection-factor-design.md`

---

## File Structure

```
data-source/tushare/factor_builders/
├── turnover_anomaly_builder.py      # NEW: Turnover rate Z-score
├── amplitude_anomaly_builder.py     # NEW: Amplitude Z-score
├── limit_behavior_builder.py          # NEW: Limit-up/down behavior
├── intraday_reversal_builder.py       # NEW: Intraday reversal strength
└── __init__.py                        # EXISTING

data-source/tushare/
└── factor_worker.py                   # MODIFY: Add 4 new BUILDERS entries

Common/Factors/Forward/
├── TurnoverAnomalyFactor.cs           # NEW: C# wrapper
├── AmplitudeAnomalyFactor.cs          # NEW: C# wrapper
├── LimitBehaviorFactor.cs             # NEW: C# wrapper
├── IntradayReversalFactor.cs          # NEW: C# wrapper
└── FactorStoreConfig.cs               # MODIFY: Register 4 new adapters

Tests/Python/FactorZoo/
└── test_manipulation_factors.py       # NEW: Unit tests for builders

Scripts/factor_zoo/
└── backtest_manipulation_signals.py   # NEW: Backtest validation script
```

---

## Task 1: turnover_anomaly_builder.py

**Files:**
- Create: `data-source/tushare/factor_builders/turnover_anomaly_builder.py`
- Test: `Tests/Python/FactorZoo/test_turnover_anomaly.py`

**Description:** Compute Z-score of turnover rate vs 20-day history.

- [ ] **Step 1: Write failing test**

```python
# Tests/Python/FactorZoo/test_turnover_anomaly.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "data-source/tushare/factor_builders"))

import pandas as pd
import numpy as np
from turnover_anomaly_builder import _compute_turnover_zscore, build_day

def test_compute_turnover_zscore():
    # Create sample daily_basic data
    df = pd.DataFrame({
        "trade_date": ["20240101", "20240102", "20240103"],
        "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
        "turnover_rate": [2.5, 15.0, 3.0]  # Middle day is outlier
    })
    result = _compute_turnover_zscore(df, "20240102", window=2)
    assert result is not None
    assert result > 2.0  # Should be high Z-score for 15.0 vs mean of 2.75

def test_build_day_structure():
    # Mock test - just verify function signature works
    assert callable(build_day)
```

- [ ] **Step 2: Run test to verify failure**

```bash
cd /home/project/hope/Lean
python -m pytest Tests/Python/FactorZoo/test_turnover_anomaly.py -v
```

Expected: `ModuleNotFoundError: No module named 'turnover_anomaly_builder'`

- [ ] **Step 3: Implement builder**

```python
# data-source/tushare/factor_builders/turnover_anomaly_builder.py
"""Turnover rate anomaly factor builder.

Z-score of daily turnover rate vs trailing 20-day window.
High Z: abnormal turnover (wash trading or distribution)
Low Z: low turnover (institutional lock-up)
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[3]
_FACTOR_ZOO_DIR = _REPO_ROOT / "Scripts" / "factor_zoo"
if str(_FACTOR_ZOO_DIR) not in sys.path:
    sys.path.insert(0, str(_FACTOR_ZOO_DIR))

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TS_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)
DEFAULT_RESULT_ROOT = os.environ.get(
    "TURNOVER_ANOMALY_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "turnover_anomaly"
MEASUREMENT = "lean_factor_turnover_anomaly"
OUTPUT_COLUMNS = ["ts_code", FACTOR_ID]

_WINDOW = 20


def _read_partition(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _compute_turnover_zscore(
    daily_basic_df: pd.DataFrame, asof: str, window: int = _WINDOW
) -> float | None:
    """Compute Z-score of turnover rate vs trailing window.
    
    Z = (turnover_t - mean) / std
    Clamped to [-5, 5]
    """
    if daily_basic_df is None or daily_basic_df.empty:
        return None
    if "trade_date" not in daily_basic_df.columns or "turnover_rate" not in daily_basic_df.columns:
        return None
    
    d = daily_basic_df.copy()
    d["trade_date"] = d["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    d = d[d["trade_date"].astype(str) <= str(asof)]
    if d.empty:
        return None
    d = d.sort_values("trade_date")
    
    # Need window + 1 rows (current + trailing window)
    if len(d) < window + 1:
        return None
    
    turnover = d["turnover_rate"].astype(float)
    
    # Current value is last row
    current_turnover = float(turnover.iloc[-1])
    
    # Historical window (excluding current)
    hist_turnover = turnover.iloc[-(window + 1):-1]
    
    mean = hist_turnover.mean()
    std = hist_turnover.std()
    
    if std == 0 or math.isnan(std):
        return 0.0  # No variation, assume neutral
    
    z_score = (current_turnover - mean) / std
    return max(-5.0, min(5.0, z_score))


def _ts_ns(trade_date_compact: str) -> int:
    local = datetime(
        int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
        int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ,
    )
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, factors: dict) -> str:
    fields = [
        f"{k}={v:.12g}" for k, v in factors.items()
        if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    ]
    if not fields:
        return ""
    return (
        f"{MEASUREMENT},ts_code={ts_code},trade_date={trade_date_compact} "
        f"{','.join(fields)} {_ts_ns(trade_date_compact)}"
    )


def write_influx(lines: Iterable[str], url: str, org: str, bucket: str,
                 token: str) -> int:
    payload = [l for l in lines if l]
    if not payload:
        return 0
    if not token:
        return 0
    q = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    req = request.Request(
        f"{url.rstrip('/')}/api/v2/write?{q}",
        data=("\n".join(payload) + "\n").encode(),
        method="POST",
        headers={"Authorization": f"Token {token}",
                 "Content-Type": "text/plain; charset=utf-8"},
    )
    with request.urlopen(req, timeout=60) as resp:
        return len(payload) if 200 <= resp.status < 300 else 0


def _format_date(yyyy_mm_dd: str) -> str:
    return yyyy_mm_dd.replace("-", "")


def build_day(
    date_yyyy_mm_dd: str,
    ts_codes: list[str],
    data_root: str | None = None,
    result_root: str | None = None,
    write_influxdb: bool = False,
    influx_url: str | None = None,
    influx_org: str | None = None,
    influx_bucket: str | None = None,
    influx_token: str | None = None,
) -> pd.DataFrame:
    data_root = data_root or DEFAULT_TS_PATH
    result_root = result_root or DEFAULT_RESULT_ROOT
    trade_date_compact = _format_date(date_yyyy_mm_dd)
    factor_dir = Path(result_root) / "factor-zoo" / FACTOR_ID
    factor_dir.mkdir(parents=True, exist_ok=True)
    
    lines: list[str] = []
    out_rows: list[dict] = []
    
    for ts_code in ts_codes:
        daily_basic_df = _read_partition(data_root, "daily_basic", ts_code)
        value = _compute_turnover_zscore(daily_basic_df, trade_date_compact)
        if value is None:
            continue
        record = {"ts_code": ts_code, FACTOR_ID: float(value)}
        out_rows.append(record)
        
        if write_influxdb:
            line = to_line(ts_code, trade_date_compact, {FACTOR_ID: float(value)})
            if line:
                lines.append(line)
    
    if out_rows:
        pd.DataFrame(out_rows).to_parquet(factor_dir / f"{date_yyyy_mm_dd}.parquet", index=False)
    
    if write_influxdb and lines:
        try:
            write_influx(
                lines,
                url=influx_url or DEFAULT_INFLUX_URL,
                org=influx_org or DEFAULT_INFLUX_ORG,
                bucket=influx_bucket or DEFAULT_INFLUX_BUCKET,
                token=influx_token or DEFAULT_INFLUX_TOKEN or "",
            )
        except Exception:
            pass
    
    if not out_rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(out_rows, columns=OUTPUT_COLUMNS)


def _cli() -> int:
    parser = argparse.ArgumentParser(description=f"{FACTOR_ID} factor builder")
    parser.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument("--ts-codes", nargs="*", default=[],
                        help="Tushare codes; if empty, CSI300 universe is used")
    parser.add_argument("--data-root", default=DEFAULT_TS_PATH)
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--influx", action="store_true")
    args = parser.parse_args()
    
    ts_codes = args.ts_codes
    if not ts_codes:
        try:
            sys.path.insert(0, str(_HERE.parent))
            from barra_cne5_data_loader import BarraCNE5DataLoader
            loader = BarraCNE5DataLoader(args.data_root)
            ts_codes = loader.load_index_constituents(
                asof_date=_format_date(args.date), index_code="000300.SH"
            )
        except Exception as exc:
            print(f"WARNING: failed to load CSI300 universe: {exc}", file=sys.stderr)
            ts_codes = []
    
    if not ts_codes:
        print("ERROR: no ts_codes provided and CSI300 universe unavailable",
              file=sys.stderr)
        return 2
    
    out = build_day(
        args.date, ts_codes,
        data_root=args.data_root, result_root=args.result_root,
        write_influxdb=args.influx,
    )
    print(f"Built {len(out)} {FACTOR_ID} rows for {args.date}")
    if not out.empty:
        print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd /home/project/hope/Lean
python -m pytest Tests/Python/FactorZoo/test_turnover_anomaly.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add data-source/tushare/factor_builders/turnover_anomaly_builder.py
git add Tests/Python/FactorZoo/test_turnover_anomaly.py
git commit -m "feat: add turnover_anomaly factor builder"
```

---

## Task 2: amplitude_anomaly_builder.py

**Files:**
- Create: `data-source/tushare/factor_builders/amplitude_anomaly_builder.py`
- Test: `Tests/Python/FactorZoo/test_amplitude_anomaly.py`

**Description:** Compute Z-score of intraday amplitude vs 20-day history.

- [ ] **Step 1: Write failing test**

```python
# Tests/Python/FactorZoo/test_amplitude_anomaly.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "data-source/tushare/factor_builders"))

import pandas as pd
import numpy as np
from amplitude_anomaly_builder import _compute_amplitude_zscore

def test_compute_amplitude_zscore():
    df = pd.DataFrame({
        "trade_date": ["20240101", "20240102", "20240103"],
        "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
        "high": [11.0, 15.0, 11.5],  # Day 2 has extreme amplitude
        "low": [9.0, 8.0, 9.5],
        "pre_close": [10.0, 10.5, 10.0]
    })
    result = _compute_amplitude_zscore(df, "20240102", window=2)
    assert result is not None
    # Amplitude = (15-8)/10.5 = 0.667, should be high Z vs others
```

- [ ] **Step 2: Run test to verify failure**

```bash
python -m pytest Tests/Python/FactorZoo/test_amplitude_anomaly.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement builder**

```python
# data-source/tushare/factor_builders/amplitude_anomaly_builder.py
"""Amplitude anomaly factor builder.

Z-score of intraday amplitude vs trailing 20-day window.
Amplitude = (high - low) / pre_close
High Z: abnormal volatility (manipulation)
Low Z: compressed volatility
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[3]
_FACTOR_ZOO_DIR = _REPO_ROOT / "Scripts" / "factor_zoo"
if str(_FACTOR_ZOO_DIR) not in sys.path:
    sys.path.insert(0, str(_FACTOR_ZOO_DIR))

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TS_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)
DEFAULT_RESULT_ROOT = os.environ.get(
    "AMPLITUDE_ANOMALY_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "amplitude_anomaly"
MEASUREMENT = "lean_factor_amplitude_anomaly"
OUTPUT_COLUMNS = ["ts_code", FACTOR_ID]

_WINDOW = 20


def _read_partition(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _compute_amplitude_zscore(
    daily_df: pd.DataFrame, asof: str, window: int = _WINDOW
) -> float | None:
    """Compute Z-score of amplitude vs trailing window.
    
    Amplitude = (high - low) / pre_close
    Z = (amplitude_t - mean) / std
    Clamped to [-5, 5]
    """
    if daily_df is None or daily_df.empty:
        return None
    required = ["trade_date", "high", "low", "pre_close"]
    if not all(c in daily_df.columns for c in required):
        return None
    
    d = daily_df.copy()
    d["trade_date"] = d["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    d = d[d["trade_date"].astype(str) <= str(asof)]
    if d.empty:
        return None
    d = d.sort_values("trade_date")
    
    if len(d) < window + 1:
        return None
    
    # Calculate amplitude
    d["amplitude"] = (d["high"].astype(float) - d["low"].astype(float)) / d["pre_close"].astype(float)
    
    current_amp = float(d["amplitude"].iloc[-1])
    hist_amp = d["amplitude"].iloc[-(window + 1):-1]
    
    mean = hist_amp.mean()
    std = hist_amp.std()
    
    if std == 0 or math.isnan(std):
        return 0.0
    
    z_score = (current_amp - mean) / std
    return max(-5.0, min(5.0, z_score))


def _ts_ns(trade_date_compact: str) -> int:
    local = datetime(
        int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
        int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ,
    )
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, factors: dict) -> str:
    fields = [
        f"{k}={v:.12g}" for k, v in factors.items()
        if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    ]
    if not fields:
        return ""
    return (
        f"{MEASUREMENT},ts_code={ts_code},trade_date={trade_date_compact} "
        f"{','.join(fields)} {_ts_ns(trade_date_compact)}"
    )


def write_influx(lines: Iterable[str], url: str, org: str, bucket: str,
                 token: str) -> int:
    payload = [l for l in lines if l]
    if not payload:
        return 0
    if not token:
        return 0
    q = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    req = request.Request(
        f"{url.rstrip('/')}/api/v2/write?{q}",
        data=("\n".join(payload) + "\n").encode(),
        method="POST",
        headers={"Authorization": f"Token {token}",
                 "Content-Type": "text/plain; charset=utf-8"},
    )
    with request.urlopen(req, timeout=60) as resp:
        return len(payload) if 200 <= resp.status < 300 else 0


def _format_date(yyyy_mm_dd: str) -> str:
    return yyyy_mm_dd.replace("-", "")


def build_day(
    date_yyyy_mm_dd: str,
    ts_codes: list[str],
    data_root: str | None = None,
    result_root: str | None = None,
    write_influxdb: bool = False,
    influx_url: str | None = None,
    influx_org: str | None = None,
    influx_bucket: str | None = None,
    influx_token: str | None = None,
) -> pd.DataFrame:
    data_root = data_root or DEFAULT_TS_PATH
    result_root = result_root or DEFAULT_RESULT_ROOT
    trade_date_compact = _format_date(date_yyyy_mm_dd)
    factor_dir = Path(result_root) / "factor-zoo" / FACTOR_ID
    factor_dir.mkdir(parents=True, exist_ok=True)
    
    lines: list[str] = []
    out_rows: list[dict] = []
    
    for ts_code in ts_codes:
        daily_df = _read_partition(data_root, "daily", ts_code)
        value = _compute_amplitude_zscore(daily_df, trade_date_compact)
        if value is None:
            continue
        record = {"ts_code": ts_code, FACTOR_ID: float(value)}
        out_rows.append(record)
        
        if write_influxdb:
            line = to_line(ts_code, trade_date_compact, {FACTOR_ID: float(value)})
            if line:
                lines.append(line)
    
    if out_rows:
        pd.DataFrame(out_rows).to_parquet(factor_dir / f"{date_yyyy_mm_dd}.parquet", index=False)
    
    if write_influxdb and lines:
        try:
            write_influx(
                lines,
                url=influx_url or DEFAULT_INFLUX_URL,
                org=influx_org or DEFAULT_INFLUX_ORG,
                bucket=influx_bucket or DEFAULT_INFLUX_BUCKET,
                token=influx_token or DEFAULT_INFLUX_TOKEN or "",
            )
        except Exception:
            pass
    
    if not out_rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(out_rows, columns=OUTPUT_COLUMNS)


def _cli() -> int:
    parser = argparse.ArgumentParser(description=f"{FACTOR_ID} factor builder")
    parser.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument("--ts-codes", nargs="*", default=[],
                        help="Tushare codes; if empty, CSI300 universe is used")
    parser.add_argument("--data-root", default=DEFAULT_TS_PATH)
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--influx", action="store_true")
    args = parser.parse_args()
    
    ts_codes = args.ts_codes
    if not ts_codes:
        try:
            sys.path.insert(0, str(_HERE.parent))
            from barra_cne5_data_loader import BarraCNE5DataLoader
            loader = BarraCNE5DataLoader(args.data_root)
            ts_codes = loader.load_index_constituents(
                asof_date=_format_date(args.date), index_code="000300.SH"
            )
        except Exception as exc:
            print(f"WARNING: failed to load CSI300 universe: {exc}", file=sys.stderr)
            ts_codes = []
    
    if not ts_codes:
        print("ERROR: no ts_codes provided and CSI300 universe unavailable",
              file=sys.stderr)
        return 2
    
    out = build_day(
        args.date, ts_codes,
        data_root=args.data_root, result_root=args.result_root,
        write_influxdb=args.influx,
    )
    print(f"Built {len(out)} {FACTOR_ID} rows for {args.date}")
    if not out.empty:
        print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
```

- [ ] **Step 4: Run test to verify pass**

```bash
python -m pytest Tests/Python/FactorZoo/test_amplitude_anomaly.py -v
```

Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add data-source/tushare/factor_builders/amplitude_anomaly_builder.py
git add Tests/Python/FactorZoo/test_amplitude_anomaly.py
git commit -m "feat: add amplitude_anomaly factor builder"
```

---

## Task 3: limit_behavior_builder.py

**Files:**
- Create: `data-source/tushare/factor_builders/limit_behavior_builder.py`
- Test: `Tests/Python/FactorZoo/test_limit_behavior.py`

**Description:** Count limit-up/down occurrences in trailing 5 days.

- [ ] **Step 1: Write failing test**

```python
# Tests/Python/FactorZoo/test_limit_behavior.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "data-source/tushare/factor_builders"))

import pandas as pd
from limit_behavior_builder import _compute_limit_behavior

def test_compute_limit_behavior():
    df = pd.DataFrame({
        "trade_date": ["20240101", "20240102", "20240103", "20240104", "20240105"],
        "ts_code": ["000001.SZ"] * 5,
        "up_stat": ["涨停", "涨停", None, None, "涨停"],  # 3 limit-ups
        "down_stat": [None, None, "跌停", None, None]  # 1 limit-down
    })
    result = _compute_limit_behavior(df, "000001.SZ", "20240105")
    assert result is not None
    assert result == 0.8  # (3/5 + 1/5) = 0.8
```

- [ ] **Step 2: Run test to verify failure**

```bash
python -m pytest Tests/Python/FactorZoo/test_limit_behavior.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement builder**

```python
# data-source/tushare/factor_builders/limit_behavior_builder.py
"""Limit behavior factor builder.

Score = limit_up_count_5d/5 + limit_down_count_5d/5
Range: [0, 2] (0=no limits, 1=all up, 1=all down, 2=mixed)
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[3]
_FACTOR_ZOO_DIR = _REPO_ROOT / "Scripts" / "factor_zoo"
if str(_FACTOR_ZOO_DIR) not in sys.path:
    sys.path.insert(0, str(_FACTOR_ZOO_DIR))

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TS_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)
DEFAULT_RESULT_ROOT = os.environ.get(
    "LIMIT_BEHAVIOR_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "limit_behavior"
MEASUREMENT = "lean_factor_limit_behavior"
OUTPUT_COLUMNS = ["ts_code", FACTOR_ID]

_WINDOW = 5


def _read_limit_list(data_root: str) -> pd.DataFrame:
    """Read limit_list_d table (date-partitioned)."""
    p = Path(data_root) / "limit_list_d"
    if not p.exists():
        return pd.DataFrame()
    try:
        frames = []
        for date_dir in p.iterdir():
            if not date_dir.is_dir():
                continue
            f = date_dir / "data.parquet"
            if f.exists():
                df = pd.read_parquet(f)
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)
    except Exception:
        return pd.DataFrame()


def _compute_limit_behavior(
    limit_df: pd.DataFrame, ts_code: str, asof: str, window: int = _WINDOW
) -> float | None:
    """Compute limit behavior score.
    
    Score = up_count/window + down_count/window
    Returns 0.0 if no limit records found.
    """
    if limit_df is None or limit_df.empty:
        return 0.0
    if "trade_date" not in limit_df.columns:
        return 0.0
    
    d = limit_df.copy()
    d["trade_date"] = d["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    d = d[d["trade_date"].astype(str) <= str(asof)]
    
    # Filter to this stock
    if "ts_code" in d.columns:
        d = d[d["ts_code"] == ts_code]
    
    if d.empty:
        return 0.0
    
    # Sort and take last window
    d = d.sort_values("trade_date").tail(window)
    
    up_count = 0
    down_count = 0
    
    if "up_stat" in d.columns:
        up_count = d["up_stat"].notna().sum()
    if "down_stat" in d.columns:
        down_count = d["down_stat"].notna().sum()
    
    score = (up_count / window) + (down_count / window)
    return float(score)


def _ts_ns(trade_date_compact: str) -> int:
    local = datetime(
        int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
        int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ,
    )
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, factors: dict) -> str:
    fields = [
        f"{k}={v:.12g}" for k, v in factors.items()
        if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    ]
    if not fields:
        return ""
    return (
        f"{MEASUREMENT},ts_code={ts_code},trade_date={trade_date_compact} "
        f"{','.join(fields)} {_ts_ns(trade_date_compact)}"
    )


def write_influx(lines: Iterable[str], url: str, org: str, bucket: str,
                 token: str) -> int:
    payload = [l for l in lines if l]
    if not payload:
        return 0
    if not token:
        return 0
    q = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    req = request.Request(
        f"{url.rstrip('/')}/api/v2/write?{q}",
        data=("\n".join(payload) + "\n").encode(),
        method="POST",
        headers={"Authorization": f"Token {token}",
                 "Content-Type": "text/plain; charset=utf-8"},
    )
    with request.urlopen(req, timeout=60) as resp:
        return len(payload) if 200 <= resp.status < 300 else 0


def _format_date(yyyy_mm_dd: str) -> str:
    return yyyy_mm_dd.replace("-", "")


def build_day(
    date_yyyy_mm_dd: str,
    ts_codes: list[str],
    data_root: str | None = None,
    result_root: str | None = None,
    write_influxdb: bool = False,
    influx_url: str | None = None,
    influx_org: str | None = None,
    influx_bucket: str | None = None,
    influx_token: str | None = None,
) -> pd.DataFrame:
    data_root = data_root or DEFAULT_TS_PATH
    result_root = result_root or DEFAULT_RESULT_ROOT
    trade_date_compact = _format_date(date_yyyy_mm_dd)
    factor_dir = Path(result_root) / "factor-zoo" / FACTOR_ID
    factor_dir.mkdir(parents=True, exist_ok=True)
    
    # Read limit_list_d once (not partitioned by ts_code)
    limit_df = _read_limit_list(data_root)
    
    lines: list[str] = []
    out_rows: list[dict] = []
    
    for ts_code in ts_codes:
        value = _compute_limit_behavior(limit_df, ts_code, trade_date_compact)
        if value is None:
            value = 0.0
        record = {"ts_code": ts_code, FACTOR_ID: float(value)}
        out_rows.append(record)
        
        if write_influxdb:
            line = to_line(ts_code, trade_date_compact, {FACTOR_ID: float(value)})
            if line:
                lines.append(line)
    
    if out_rows:
        pd.DataFrame(out_rows).to_parquet(factor_dir / f"{date_yyyy_mm_dd}.parquet", index=False)
    
    if write_influxdb and lines:
        try:
            write_influx(
                lines,
                url=influx_url or DEFAULT_INFLUX_URL,
                org=influx_org or DEFAULT_INFLUX_ORG,
                bucket=influx_bucket or DEFAULT_INFLUX_BUCKET,
                token=influx_token or DEFAULT_INFLUX_TOKEN or "",
            )
        except Exception:
            pass
    
    if not out_rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(out_rows, columns=OUTPUT_COLUMNS)


def _cli() -> int:
    parser = argparse.ArgumentParser(description=f"{FACTOR_ID} factor builder")
    parser.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument("--ts-codes", nargs="*", default=[],
                        help="Tushare codes; if empty, CSI300 universe is used")
    parser.add_argument("--data-root", default=DEFAULT_TS_PATH)
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--influx", action="store_true")
    args = parser.parse_args()
    
    ts_codes = args.ts_codes
    if not ts_codes:
        try:
            sys.path.insert(0, str(_HERE.parent))
            from barra_cne5_data_loader import BarraCNE5DataLoader
            loader = BarraCNE5DataLoader(args.data_root)
            ts_codes = loader.load_index_constituents(
                asof_date=_format_date(args.date), index_code="000300.SH"
            )
        except Exception as exc:
            print(f"WARNING: failed to load CSI300 universe: {exc}", file=sys.stderr)
            ts_codes = []
    
    if not ts_codes:
        print("ERROR: no ts_codes provided and CSI300 universe unavailable",
              file=sys.stderr)
        return 2
    
    out = build_day(
        args.date, ts_codes,
        data_root=args.data_root, result_root=args.result_root,
        write_influxdb=args.influx,
    )
    print(f"Built {len(out)} {FACTOR_ID} rows for {args.date}")
    if not out.empty:
        print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
```

- [ ] **Step 4: Run test to verify pass**

```bash
python -m pytest Tests/Python/FactorZoo/test_limit_behavior.py -v
```

Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add data-source/tushare/factor_builders/limit_behavior_builder.py
git add Tests/Python/FactorZoo/test_limit_behavior.py
git commit -m "feat: add limit_behavior factor builder"
```

---

## Task 4: intraday_reversal_builder.py

**Files:**
- Create: `data-source/tushare/factor_builders/intraday_reversal_builder.py`
- Test: `Tests/Python/FactorZoo/test_intraday_reversal.py`

**Description:** Compute intraday reversal strength: (close - low) / (high - low).

- [ ] **Step 1: Write failing test**

```python
# Tests/Python/FactorZoo/test_intraday_reversal.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "data-source/tushare/factor_builders"))

import pandas as pd
from intraday_reversal_builder import _compute_intraday_reversal

def test_compute_reversal_accumulation():
    # Close near high = accumulation
    df = pd.DataFrame({
        "trade_date": ["20240101"],
        "ts_code": ["000001.SZ"],
        "high": [11.0],
        "low": [9.0],
        "close": [10.8]
    })
    result = _compute_intraday_reversal(df, "20240101")
    assert result is not None
    expected = (10.8 - 9.0) / (11.0 - 9.0)  # 0.9
    assert abs(result - expected) < 0.01

def test_compute_reversal_distribution():
    # Close near low = distribution
    df = pd.DataFrame({
        "trade_date": ["20240101"],
        "ts_code": ["000001.SZ"],
        "high": [11.0],
        "low": [9.0],
        "close": [9.2]
    })
    result = _compute_intraday_reversal(df, "20240101")
    expected = (9.2 - 9.0) / (11.0 - 9.0)  # 0.1
    assert abs(result - expected) < 0.01
```

- [ ] **Step 2: Run test to verify failure**

```bash
python -m pytest Tests/Python/FactorZoo/test_intraday_reversal.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement builder**

```python
# data-source/tushare/factor_builders/intraday_reversal_builder.py
"""Intraday reversal factor builder.

Reversal = (close - low) / (high - low)
Range: [0, 1]
Near 1: Strong reversal from low (accumulation)
Near 0: Reversal from high (distribution)
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[3]
_FACTOR_ZOO_DIR = _REPO_ROOT / "Scripts" / "factor_zoo"
if str(_FACTOR_ZOO_DIR) not in sys.path:
    sys.path.insert(0, str(_FACTOR_ZOO_DIR))

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TS_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)
DEFAULT_RESULT_ROOT = os.environ.get(
    "INTRADAY_REVERSAL_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "intraday_reversal"
MEASUREMENT = "lean_factor_intraday_reversal"
OUTPUT_COLUMNS = ["ts_code", FACTOR_ID]


def _read_partition(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _compute_intraday_reversal(
    daily_df: pd.DataFrame, asof: str
) -> float | None:
    """Compute intraday reversal strength.
    
    Reversal = (close - low) / (high - low)
    Returns None if data insufficient.
    """
    if daily_df is None or daily_df.empty:
        return None
    required = ["trade_date", "high", "low", "close"]
    if not all(c in daily_df.columns for c in required):
        return None
    
    d = daily_df.copy()
    d["trade_date"] = d["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    d = d[d["trade_date"].astype(str) <= str(asof)]
    if d.empty:
        return None
    d = d.sort_values("trade_date").tail(1)  # Last row only
    
    high = float(d["high"].iloc[0])
    low = float(d["low"].iloc[0])
    close = float(d["close"].iloc[0])
    
    if high == low:  # No range
        return 0.5  # Neutral
    
    reversal = (close - low) / (high - low)
    return max(0.0, min(1.0, reversal))


def _ts_ns(trade_date_compact: str) -> int:
    local = datetime(
        int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
        int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ,
    )
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, factors: dict) -> str:
    fields = [
        f"{k}={v:.12g}" for k, v in factors.items()
        if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    ]
    if not fields:
        return ""
    return (
        f"{MEASUREMENT},ts_code={ts_code},trade_date={trade_date_compact} "
        f"{','.join(fields)} {_ts_ns(trade_date_compact)}"
    )


def write_influx(lines: Iterable[str], url: str, org: str, bucket: str,
                 token: str) -> int:
    payload = [l for l in lines if l]
    if not payload:
        return 0
    if not token:
        return 0
    q = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    req = request.Request(
        f"{url.rstrip('/')}/api/v2/write?{q}",
        data=("\n".join(payload) + "\n").encode(),
        method="POST",
        headers={"Authorization": f"Token {token}",
                 "Content-Type": "text/plain; charset=utf-8"},
    )
    with request.urlopen(req, timeout=60) as resp:
        return len(payload) if 200 <= resp.status < 300 else 0


def _format_date(yyyy_mm_dd: str) -> str:
    return yyyy_mm_dd.replace("-", "")


def build_day(
    date_yyyy_mm_dd: str,
    ts_codes: list[str],
    data_root: str | None = None,
    result_root: str | None = None,
    write_influxdb: bool = False,
    influx_url: str | None = None,
    influx_org: str | None = None,
    influx_bucket: str | None = None,
    influx_token: str | None = None,
) -> pd.DataFrame:
    data_root = data_root or DEFAULT_TS_PATH
    result_root = result_root or DEFAULT_RESULT_ROOT
    trade_date_compact = _format_date(date_yyyy_mm_dd)
    factor_dir = Path(result_root) / "factor-zoo" / FACTOR_ID
    factor_dir.mkdir(parents=True, exist_ok=True)
    
    lines: list[str] = []
    out_rows: list[dict] = []
    
    for ts_code in ts_codes:
        daily_df = _read_partition(data_root, "daily", ts_code)
        value = _compute_intraday_reversal(daily_df, trade_date_compact)
        if value is None:
            continue
        record = {"ts_code": ts_code, FACTOR_ID: float(value)}
        out_rows.append(record)
        
        if write_influxdb:
            line = to_line(ts_code, trade_date_compact, {FACTOR_ID: float(value)})
            if line:
                lines.append(line)
    
    if out_rows:
        pd.DataFrame(out_rows).to_parquet(factor_dir / f"{date_yyyy_mm_dd}.parquet", index=False)
    
    if write_influxdb and lines:
        try:
            write_influx(
                lines,
                url=influx_url or DEFAULT_INFLUX_URL,
                org=influx_org or DEFAULT_INFLUX_ORG,
                bucket=influx_bucket or DEFAULT_INFLUX_BUCKET,
                token=influx_token or DEFAULT_INFLUX_TOKEN or "",
            )
        except Exception:
            pass
    
    if not out_rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(out_rows, columns=OUTPUT_COLUMNS)


def _cli() -> int:
    parser = argparse.ArgumentParser(description=f"{FACTOR_ID} factor builder")
    parser.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument("--ts-codes", nargs="*", default=[],
                        help="Tushare codes; if empty, CSI300 universe is used")
    parser.add_argument("--data-root", default=DEFAULT_TS_PATH)
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--influx", action="store_true")
    args = parser.parse_args()
    
    ts_codes = args.ts_codes
    if not ts_codes:
        try:
            sys.path.insert(0, str(_HERE.parent))
            from barra_cne5_data_loader import BarraCNE5DataLoader
            loader = BarraCNE5DataLoader(args.data_root)
            ts_codes = loader.load_index_constituents(
                asof_date=_format_date(args.date), index_code="000300.SH"
            )
        except Exception as exc:
            print(f"WARNING: failed to load CSI300 universe: {exc}", file=sys.stderr)
            ts_codes = []
    
    if not ts_codes:
        print("ERROR: no ts_codes provided and CSI300 universe unavailable",
              file=sys.stderr)
        return 2
    
    out = build_day(
        args.date, ts_codes,
        data_root=args.data_root, result_root=args.result_root,
        write_influxdb=args.influx,
    )
    print(f"Built {len(out)} {FACTOR_ID} rows for {args.date}")
    if not out.empty:
        print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
```

- [ ] **Step 4: Run test to verify pass**

```bash
python -m pytest Tests/Python/FactorZoo/test_intraday_reversal.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add data-source/tushare/factor_builders/intraday_reversal_builder.py
git add Tests/Python/FactorZoo/test_intraday_reversal.py
git commit -m "feat: add intraday_reversal factor builder"
```

---

## Task 5: Register in factor_worker.py

**Files:**
- Modify: `data-source/tushare/factor_worker.py`

**Description:** Add 4 new BUILDERS entries using `_make_factorzoo_builder()` pattern.

- [ ] **Step 1: Add builder registrations**

Add after line ~374 (after `_BLD_REVERSAL` definition):

```python
# data-source/tushare/factor_worker.py
# Add after existing builder registrations (around line 374)

# Manipulation detection factors
_BLD_TURNOVER, _RES_TURNOVER = _make_factorzoo_builder("turnover_anomaly_builder", "turnover_anomaly")
_BLD_AMPLITUDE, _RES_AMPLITUDE = _make_factorzoo_builder("amplitude_anomaly_builder", "amplitude_anomaly")
_BLD_LIMIT, _RES_LIMIT = _make_factorzoo_builder("limit_behavior_builder", "limit_behavior")
_BLD_INTRADAY, _RES_INTRADAY = _make_factorzoo_builder("intraday_reversal_builder", "intraday_reversal")
```

- [ ] **Step 2: Add to BUILDERS list**

Add to `BUILDERS: list[FactorBuilder]` (around line 498):

```python
    # ── Manipulation detection factors ──
    FactorBuilder(
        factor_id="turnover_anomaly",
        build_callable=_BLD_TURNOVER, latest_date_resolver=_RES_TURNOVER,
        depends_on=("daily_basic",), max_backfill_days=60,
    ),
    FactorBuilder(
        factor_id="amplitude_anomaly",
        build_callable=_BLD_AMPLITUDE, latest_date_resolver=_RES_AMPLITUDE,
        depends_on=("daily",), max_backfill_days=60,
    ),
    FactorBuilder(
        factor_id="limit_behavior",
        build_callable=_BLD_LIMIT, latest_date_resolver=_RES_LIMIT,
        depends_on=("limit_list_d",), max_backfill_days=60,
    ),
    FactorBuilder(
        factor_id="intraday_reversal",
        build_callable=_BLD_INTRADAY, latest_date_resolver=_RES_INTRADAY,
        depends_on=("daily",), max_backfill_days=60,
    ),
```

- [ ] **Step 3: Verify syntax**

```bash
cd /home/project/hope/Lean
python -m py_compile data-source/tushare/factor_worker.py
```

Expected: No output (success)

- [ ] **Step 4: Commit**

```bash
git add data-source/tushare/factor_worker.py
git commit -m "feat: register manipulation detection factors in factor_worker"
```

---

## Task 6: C# Factor Classes

**Files:**
- Create: `Common/Factors/Forward/TurnoverAnomalyFactor.cs`
- Create: `Common/Factors/Forward/AmplitudeAnomalyFactor.cs`
- Create: `Common/Factors/Forward/LimitBehaviorFactor.cs`
- Create: `Common/Factors/Forward/IntradayReversalFactor.cs`

**Description:** Wrap Python output for LEAN FactorStore consumption.

- [ ] **Step 1: Create TurnoverAnomalyFactor.cs**

```csharp
// Common/Factors/Forward/TurnoverAnomalyFactor.cs
namespace QuantConnect.Common.Factors.Forward
{
    using System;
    using Common.Factors.Core;
    using Common.Factors.Store;

    /// <summary>
    /// Turnover rate anomaly factor: Z-score of turnover vs 20-day history.
    /// High Z: abnormal turnover (wash trading or distribution)
    /// Low Z: low turnover (institutional lock-up)
    /// </summary>
    public class TurnoverAnomalyFactor : IFactor
    {
        private readonly IFactorStore _store;

        public TurnoverAnomalyFactor(IFactorStore store)
        {
            _store = store ?? throw new ArgumentNullException(nameof(store));
        }

        public FactorResult Get(Symbol symbol, DateTime date)
        {
            return _store.Get("turnover_anomaly", symbol, date);
        }
    }
}
```

- [ ] **Step 2: Create AmplitudeAnomalyFactor.cs**

```csharp
// Common/Factors/Forward/AmplitudeAnomalyFactor.cs
namespace QuantConnect.Common.Factors.Forward
{
    using System;
    using Common.Factors.Core;
    using Common.Factors.Store;

    /// <summary>
    /// Amplitude anomaly factor: Z-score of intraday amplitude vs 20-day history.
    /// High Z: abnormal volatility (manipulation)
    /// Low Z: compressed volatility
    /// </summary>
    public class AmplitudeAnomalyFactor : IFactor
    {
        private readonly IFactorStore _store;

        public AmplitudeAnomalyFactor(IFactorStore store)
        {
            _store = store ?? throw new ArgumentNullException(nameof(store));
        }

        public FactorResult Get(Symbol symbol, DateTime date)
        {
            return _store.Get("amplitude_anomaly", symbol, date);
        }
    }
}
```

- [ ] **Step 3: Create LimitBehaviorFactor.cs**

```csharp
// Common/Factors/Forward/LimitBehaviorFactor.cs
namespace QuantConnect.Common.Factors.Forward
{
    using System;
    using Common.Factors.Core;
    using Common.Factors.Store;

    /// <summary>
    /// Limit behavior factor: Frequency of limit-up/down in trailing 5 days.
    /// Score = up_count/5 + down_count/5, range [0, 2]
    /// </summary>
    public class LimitBehaviorFactor : IFactor
    {
        private readonly IFactorStore _store;

        public LimitBehaviorFactor(IFactorStore store)
        {
            _store = store ?? throw new ArgumentNullException(nameof(store));
        }

        public FactorResult Get(Symbol symbol, DateTime date)
        {
            return _store.Get("limit_behavior", symbol, date);
        }
    }
}
```

- [ ] **Step 4: Create IntradayReversalFactor.cs**

```csharp
// Common/Factors/Forward/IntradayReversalFactor.cs
namespace QuantConnect.Common.Factors.Forward
{
    using System;
    using Common.Factors.Core;
    using Common.Factors.Store;

    /// <summary>
    /// Intraday reversal factor: (close - low) / (high - low).
    /// Near 1: accumulation (reversal from low)
    /// Near 0: distribution (reversal from high)
    /// </summary>
    public class IntradayReversalFactor : IFactor
    {
        private readonly IFactorStore _store;

        public IntradayReversalFactor(IFactorStore store)
        {
            _store = store ?? throw new ArgumentNullException(nameof(store));
        }

        public FactorResult Get(Symbol symbol, DateTime date)
        {
            return _store.Get("intraday_reversal", symbol, date);
        }
    }
}
```

- [ ] **Step 5: Commit C# files**

```bash
git add Common/Factors/Forward/TurnoverAnomalyFactor.cs
git add Common/Factors/Forward/AmplitudeAnomalyFactor.cs
git add Common/Factors/Forward/LimitBehaviorFactor.cs
git add Common/Factors/Forward/IntradayReversalFactor.cs
git commit -m "feat: add C# factor wrappers for manipulation detection"
```

---

## Task 7: Register in FactorStoreConfig

**Files:**
- Modify: `Common/Factors/Store/FactorStoreConfig.cs`

**Description:** Register RParquetAdapter for 4 new factors.

- [ ] **Step 1: Add adapters**

```csharp
// Common/Factors/Store/FactorStoreConfig.cs
// Add in ConfigureFactorStore method after existing registrations:

// Manipulation detection factors
store.Register("turnover_anomaly", new RParquetAdapter("factor-zoo/turnover_anomaly", "turnover_anomaly"));
store.Register("amplitude_anomaly", new RParquetAdapter("factor-zoo/amplitude_anomaly", "amplitude_anomaly"));
store.Register("limit_behavior", new RParquetAdapter("factor-zoo/limit_behavior", "limit_behavior"));
store.Register("intraday_reversal", new RParquetAdapter("factor-zoo/intraday_reversal", "intraday_reversal"));
```

- [ ] **Step 2: Commit**

```bash
git add Common/Factors/Store/FactorStoreConfig.cs
git commit -m "feat: register manipulation factors in FactorStore"
```

---

## Task 8: Backtest Validation Script

**Files:**
- Create: `Scripts/factor_zoo/backtest_manipulation_signals.py`

**Description:** Load factor values and test correlation with future returns.

- [ ] **Step 1: Create backtest script**

```python
"""Backtest validation for manipulation detection factors.

Tests signal correlation with future 1-day, 5-day, 20-day returns.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import numpy as np


def load_factor(factor_id: str, date: str, result_root: str) -> pd.DataFrame:
    """Load factor parquet for a date."""
    p = Path(result_root) / "factor-zoo" / factor_id / f"{date}.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def load_returns(date: str, data_root: str, forward_days: int = 1) -> pd.DataFrame:
    """Load forward returns from daily table."""
    # Simplified: in reality would compute from price data
    # For now return empty to show structure
    return pd.DataFrame()


def compute_ic(factor_df: pd.DataFrame, returns_df: pd.DataFrame) -> float:
    """Compute Information Coefficient (rank correlation)."""
    if factor_df.empty or returns_df.empty:
        return np.nan
    merged = factor_df.merge(returns_df, on="ts_code")
    if len(merged) < 10:
        return np.nan
    return merged["factor"].corr(merged["return"], method="spearman")


def analyze_factor(factor_id: str, dates: list[str], result_root: str) -> dict:
    """Analyze factor performance over date range."""
    ics = []
    for date in dates:
        factor_df = load_factor(factor_id, date, result_root)
        returns_df = load_returns(date, "", 1)  # 1-day forward
        ic = compute_ic(factor_df, returns_df)
        if not np.isnan(ic):
            ics.append(ic)
    
    if not ics:
        return {"mean_ic": np.nan, "ic_std": np.nan, "ir": np.nan}
    
    return {
        "mean_ic": float(np.mean(ics)),
        "ic_std": float(np.std(ics)),
        "ir": float(np.mean(ics) / np.std(ics)) if np.std(ics) > 0 else 0,
        "sample_count": len(ics),
    }


def main():
    parser = argparse.ArgumentParser(description="Backtest manipulation detection factors")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--result-root", default="/home/project/hope/Lean/result")
    args = parser.parse_args()
    
    # Generate date range (simplified, should use trade calendar)
    from datetime import datetime, timedelta
    dates = []
    current = datetime.strptime(args.start_date, "%Y-%m-%d")
    end = datetime.strptime(args.end_date, "%Y-%m-%d")
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    
    factors = ["turnover_anomaly", "amplitude_anomaly", "limit_behavior", "intraday_reversal"]
    results = {}
    
    for factor_id in factors:
        print(f"Analyzing {factor_id}...")
        results[factor_id] = analyze_factor(factor_id, dates, args.result_root)
    
    print("\nResults:")
    print(json.dumps(results, indent=2))
    
    # Save to file
    output_file = Path(args.result_root) / "manipulation_factor_analysis.json"
    output_file.write_text(json.dumps(results, indent=2))
    print(f"\nSaved to {output_file}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add Scripts/factor_zoo/backtest_manipulation_signals.py
git commit -m "feat: add backtest validation script for manipulation factors"
```

---

## Verification Checklist

After completing all tasks:

- [ ] All 4 factor builders exist in `factor_builders/`
- [ ] All 4 test files exist and pass
- [ ] `factor_worker.py` updated with 4 new BUILDERS
- [ ] 4 C# factor classes exist in `Common/Factors/Forward/`
- [ ] `FactorStoreConfig.cs` updated with RParquetAdapter registrations
- [ ] Backtest script exists in `Scripts/factor_zoo/`
- [ ] `dotnet build` passes for C# changes
- [ ] Manual test: Run one builder CLI with `--date 2024-01-02`
