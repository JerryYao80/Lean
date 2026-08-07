# A-Share ETF Trading Implementation

## Overview

This implementation adds comprehensive support for Chinese A-share ETF trading to the LEAN algorithmic trading engine, with explicit focus on T+0 (same-day buy/sell) ETFs.

## Implementation Date

2026-03-06

## Features Implemented

### 1. Market Support

**Markets Added:**
- `Market.SSE` - Shanghai Stock Exchange (上海证券交易所)
- `Market.SZSE` - Shenzhen Stock Exchange (深圳证券交易所)

**Currency:**
- Added `CNY` (Chinese Yuan) support to `Currencies.cs`

**Market Hours:**
- Morning session: 09:30 - 11:30 CST
- Afternoon session: 13:00 - 15:00 CST
- Configured in `market-hours-database.json`

### 2. T+0 ETF Registry

**File:** `Common/Securities/Equity/AShareETFMetadata.cs`

**Supported T+0 ETFs (9 total):**

| Ticker | Name | Market | Type |
|--------|------|--------|------|
| 510050 | 50ETF | SSE | 股票型 |
| 510300 | 300ETF | SSE | 股票型 |
| 510500 | 500ETF | SSE | 股票型 |
| 518880 | 黄金ETF | SSE | 商品型 |
| 511880 | 银华日利 | SSE | 货币型 |
| 511990 | 华宝添益 | SSE | 货币型 |
| 159915 | 创业板ETF | SZSE | 股票型 |
| 159919 | 300ETF | SZSE | 股票型 |
| 159949 | 创业板50 | SZSE | 股票型 |

**Key Features:**
- T+0 classification (same-day buy/sell allowed)
- Excludes T+1 ETFs (next-day sell only)
- Metadata includes: name, market, type, settlement model

### 3. Custom A-Share Models

#### AShareETFFeeModel
**File:** `Common/Orders/Fees/AShareETFFeeModel.cs`

**Fee Structure:**
- Commission rate: 0.03% (3 basis points)
- Minimum commission: 5 CNY
- Transfer fee (SSE only): 0.002% (0.2 basis points)
- Returns fees in CNY currency

#### AShareETFFillModel
**File:** `Common/Orders/Fills/AShareETFFillModel.cs`

**Fill Rules:**
- Price limits: symbol-specific daily bands (10% for most ETFs, 20% for configured growth-board ETFs)
- Lot size: 100 shares (1手)
- Validates quantity is multiple of 100
- Enforces price limit boundaries

#### AShareETFBuyingPowerModel
**File:** `Common/Securities/AShareETFBuyingPowerModel.cs`

**Buying Power Rules:**
- Enforces lot size (100 shares)
- Rounds quantities to nearest lot
- Validates order quantities

#### AShareETF Security Class
**File:** `Common/Securities/Equity/AShareETF.cs`

**Features:**
- Immediate settlement (T+0 trading)
- Symbol-specific price-limit configuration with tick-size rounding
- Lot size validation
- Integrates all custom models

### 4. Tushare Data Integration

#### TushareHistoryProvider
**File:** `Engine/HistoricalData/TushareHistoryProvider.cs`

**Capabilities:**
- Reads historical data from Tushare Parquet files
- Converts Tushare ts_code format (e.g., "510050.SH") to LEAN Symbols
- Supports daily resolution
- Integrates with LEAN's History API

**Data Path:**
- Expects Parquet files at: `Data/fund_daily/ts_code={ticker}/data.parquet`
- Example: `Data/fund_daily/ts_code=510050.SH/data.parquet`

#### TushareDataConverter
**File:** `Common/Data/TushareDataConverter.cs`

**Functions:**
- Converts Tushare Parquet data to LEAN TradeBar format
- Handles date/time conversion (YYYYMMDD → UTC)
- Converts volume from 手 (lots) to shares (×100)
- Uses Python pandas for Parquet reading

**Price Conversion:**
- Tushare prices are in CNY (e.g., 4.66)
- Converts trade_date to market close time (15:00 CST)

#### TushareDataQueue
**File:** `Engine/DataFeeds/Queues/TushareDataQueue.cs`

**Status:** Placeholder implementation
- Returns empty enumerables
- Intended for future live data feed integration

#### TushareDataCache
**File:** `Common/Data/TushareDataCache.cs`

**Purpose:**
- Caches ETF metadata
- Provides T+0 ETF symbol lists
- Optimizes repeated metadata lookups

### 5. ETF Momentum Strategy

**File:** `Algorithm.CSharp/ETFMomentumStrategy.cs`

**Strategy Logic:**
1. Loads all T+0 ETFs from registry (excludes T+1 ETFs)
2. Calculates 20-day momentum for each ETF
3. Selects top 3 ETFs by momentum
4. Rebalances portfolio every day (30 minutes after market open)
5. Equal weight allocation (33.33% each)

**Parameters:**
- Lookback period: 20 days
- Rebalance frequency: Daily
- Top N ETFs: 3
- Lot size: 100 shares

**Custom Models Applied:**
- AShareETFFeeModel for commission calculation
- AShareETFFillModel for order execution
- AShareETFBuyingPowerModel for position sizing

### 6. Configuration Files

#### config-ashare-etf-backtest.json
**File:** `Launcher/config/config-ashare-etf-backtest.json`

**Settings:**
- Algorithm: ETFMomentumStrategy
- Environment: backtesting
- Data folder: ../../../Data
- History provider: TushareHistoryProvider

#### config-ashare-etf-live-paper.json
**File:** `Launcher/config/config-ashare-etf-live-paper.json`

**Settings:**
- Algorithm: ETFMomentumStrategy
- Environment: live-paper
- Data queue: TushareDataQueue (placeholder)

## Testing Results

### Backtest Performance (2024-01-01 to 2024-12-31)

**Data Processing:**
- Total data points: 6,770
- Processing speed: ~0k data points/second
- Execution time: ~360 seconds

**Trading Activity:**
- Total orders submitted: 50+
- Orders filled: Multiple successful fills
- Order types: Market orders (buy/sell)

**Momentum Calculations:**
- Successfully calculated 20-day momentum
- Example values: 510050: +6.76%, 159919: +4.41%, 518880: +0.67%
- History API loaded 20 bars per ETF successfully

**Order Execution:**
- Pre-fix backtest logs showed incorrect tiny fill prices because the LEAN daily zip format was wrong.
- After the data-format fix, fills must be revalidated on a rebuilt `.NET 10` runtime before quoting new execution examples here.
- Fee calculation remains denominated in CNY.

## Known Issues

### 1. Backtest Runtime Prerequisite

**Current Constraint:**
- The solution targets `.NET 10`.
- This environment currently only has `.NET 6` installed at `/usr/local/dotnet`.
- Source fixes are in place, but a local `.NET 10` SDK/runtime is still required to rebuild and rerun the launcher in this environment.

### 2. Live-paper Queue Is Still Simulated

**Current Constraint:**
- `Engine/DataFeeds/Queues/TushareDataQueue.cs` still simulates live-paper updates from parquet-backed daily bars.
- It now respects `tushare-data-path`, but it is not yet a real streaming integration.

### 3. History Availability Still Depends on Market Calendar

**Current Constraint:**
- The strategy still skips rebalances whenever fewer than the required lookback bars are available.
- That behavior is expected around the start of the backtest or around real market holidays.
- This is no longer part of the 10,000x pricing bug.

## Data Requirements

### Tushare Data Setup

**Required Data:**
- Tushare fund_daily data in Parquet format
- Directory structure: `fund_daily/ts_code={ticker}/data.parquet`

**Data Location:**
- Recommended: `/home/project/tushare-downloader/tushare_data/fund_daily/`
- LEAN expects: `{DataFolder}/fund_daily/`

**Symlink Setup:**
```bash
ln -sf /home/project/tushare-downloader/tushare_data/fund_daily \
       /home/project/hope/Lean/Data/fund_daily
```

**Data Format:**
- File format: Apache Parquet
- Columns: ts_code, trade_date, open, high, low, close, vol, amount, etc.
- Date format: YYYYMMDD (e.g., 20240102)
- Price unit: CNY
- Volume unit: 手 (lots, 100 shares each)

### Python Dependencies

**Required for Parquet Reading:**
```bash
pip install pandas pyarrow
```

**Python Environment:**
- Path: `/root/miniconda3/envs/quant311/bin/python`
- Configured in TushareDataConverter.cs

## Usage

### Running Backtest

```bash
cd Lean/Launcher/bin/Debug
dotnet QuantConnect.Lean.Launcher.dll \
  --config ../../../Launcher/config/config-ashare-etf-backtest.json
```

### Running Paper Trading

```bash
cd Lean/Launcher/bin/Debug
dotnet QuantConnect.Lean.Launcher.dll \
  --config ../../../Launcher/config/config-ashare-etf-live-paper.json
```

### Building the Project

```bash
cd Lean
dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj -c Debug
```

## Architecture

### Data Flow

```
Tushare Parquet Files
    ↓
TushareDataConverter (Python pandas)
    ↓
TushareHistoryProvider
    ↓
LEAN History API
    ↓
ETFMomentumStrategy
    ↓
Order Execution (with custom models)
    ↓
Backtest Results
```

### Component Dependencies

```
ETFMomentumStrategy
    ├── AShareETFMetadata (registry)
    ├── TushareHistoryProvider (data)
    ├── AShareETFFeeModel (fees)
    ├── AShareETFFillModel (fills)
    └── AShareETFBuyingPowerModel (buying power)

TushareHistoryProvider
    ├── TushareDataConverter
    └── TushareDataCache

TushareDataConverter
    └── Python pandas + pyarrow
```

## Future Enhancements

### High Priority

1. **Fix Price Scaling Issue**
   - Investigate SymbolProperties database
   - Implement correct price magnifier
   - Test with various ETF types

2. **Complete TushareDataQueue**
   - Implement real-time data feed
   - Connect to Tushare API
   - Handle live market data

3. **Add More T+0 ETFs**
   - Expand registry beyond 9 ETFs
   - Include sector ETFs
   - Add commodity ETFs

### Medium Priority

4. **Improve Error Handling**
   - Better handling of data gaps
   - Retry logic for Parquet reading
   - Graceful degradation

5. **Performance Optimization**
   - Cache Parquet data in memory
   - Optimize History API calls
   - Reduce Python subprocess overhead

6. **Add Risk Management**
   - Position size limits
   - Stop loss orders
   - Maximum drawdown controls

### Low Priority

7. **Add More Strategies**
   - Mean reversion
   - Pairs trading
   - Statistical arbitrage

8. **Enhance Logging**
   - Structured logging
   - Performance metrics
   - Trade analytics

## References

### LEAN Documentation
- Official docs: https://www.lean.io/docs/
- GitHub: https://github.com/QuantConnect/Lean

### Tushare Documentation
- Official site: https://tushare.pro/
- API docs: https://tushare.pro/document/2

### A-Share Market Rules
- T+0 ETF list: Check with exchanges
- Trading hours: 09:30-15:00 CST (with lunch break)
- Lot size: 100 shares (1手)
- Price limits: ±10% for most ETFs, 20% for configured growth-board ETFs

## Contributors

- Implementation: Claude (Anthropic)
- Testing: Automated backtests
- Date: 2026-03-06

## License

This implementation follows the LEAN project's Apache 2.0 license.
