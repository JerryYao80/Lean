# A-Share ETF Implementation - Follow-up Tasks

## Status Summary

**Updated:** 2026-03-07

### Critical Issue: Price Scaling (Resolved)

**Actual Root Cause**
- The backtest was reading `Data/equity/{sse|szse}/daily/*.zip` for market prices and order fills.
- LEAN parses daily `equity` zip data with an internal `1/10000` scale factor.
- Our A-share ETF zip files incorrectly stored decimal yuan prices such as `4.666` instead of LEAN-scaled integers such as `46660`.
- That made `Securities[symbol].Price` and fill prices 10,000x too small.
- A previous follow-up attempt then multiplied parquet history prices by `10000` in `TushareDataConverter`, which made History API prices 10,000x too large and hid the real source of the bug.

**Wrong Fixes We Must Not Repeat**
- Do **not** multiply parquet prices by `10000` in `Common/Data/TushareDataConverter.cs`.
- Do **not** add `PriceMagnifier = 10000` for `sse` or `szse` in `Data/symbol-properties/symbol-properties-database.csv`.
- Do **not** blame currency conversion before checking the on-disk LEAN daily zip format.

## Completed In This Pass

- `Common/Data/TushareDataConverter.cs`
  - Restored raw CNY prices from parquet for History API.
- `Engine/HistoricalData/TushareHistoryProvider.cs`
  - Now reads `tushare-data-path` from job parameters instead of relying only on `Data/fund_daily` symlinks.
- `Engine/DataFeeds/Queues/TushareDataQueue.cs`
  - Now reads `tushare-data-path` from job parameters too.
- `Scripts/convert-tushare-to-lean.py`
  - Now writes both reference `.csv` files and LEAN-compatible `.zip` files.
  - Zip rows are written as scaled integers.
  - Added zip validation so bad decimal-price zips fail fast.
- `Algorithm.CSharp/ETFMomentumStrategy.cs`
  - Stops logging misleading `0.0000` initialization prices as real market prices.
  - Skips orders when the current price is zero.
  - Skips orders when the current price is inconsistent with recent history by more than 10x.
  - Skips orders that would overflow `Int32` quantities.
  - Rebalances in sell-first / buy-later order and keeps a cash buffer for daily MOO execution.
- `Scripts/prepare-tushare-data.py`
  - Next-step instructions now include running the conversion script before backtests.
- `Common/Securities/Equity/AShareETFMetadata.cs` + `Common/Securities/Equity/AShareETF.cs`
  - Price-limit percentages are now symbol-specific instead of hard-coded to 10% for every ETF.
  - 创业板 ETFs (`159915`, `159949`) now use a 20% band.
  - Limit prices are rounded to the ETF minimum price variation before validation.
  - ETF limit validation now falls back to the ETF tick size (`0.001`) when generic equity symbol properties are too coarse.
- `Common/Orders/Fills/AShareETFFillModel.cs`
  - Uses the previous session close plus symbol-specific, tick-rounded price limits.
- `Tests/Common/Securities/AShareETFTests.cs`
  - Adds regression coverage for growth-board ETF limits, tick rounding, and the ETF tick-size fallback.

## Required Runbook

1. Refresh parquet data if needed.
2. Run `python3 Scripts/convert-tushare-to-lean.py`.
3. Confirm the script finishes with zip validation success.
4. Build LEAN with `/usr/local/dotnet/dotnet build QuantConnect.Lean.sln`.
5. Rebuild before every verification run so `Launcher/bin/Debug/*.dll` matches the edited source.
6. Run the backtest with `/usr/local/dotnet/dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-ashare-etf-backtest.json` from `Launcher/bin/Debug`.

## Latest Verification

- **Backtest rerun:** 2026-03-07 with `/usr/local/dotnet/dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-ashare-etf-backtest.json`
- **Result:** completed successfully in 8.26 seconds on 46,568 data points
- **Portfolio outcome:** final portfolio value `¥1,203,491.95`, total return `20.35%`, total orders `147`
- **Regression check:** no failed data requests, no `Order invalid`, no zero-price initialization logs treated as live prices, and fills remained in normal CNY ranges such as `2.754`, `4.178`, `4.066`, `2.678`, `1.211`
- **Environment note:** this machine should use `/usr/local/dotnet/dotnet`; avoid assuming `dotnet` is on `PATH`
- **Build note:** an earlier rerun still showed old invalid orders because it was using stale DLLs from `Launcher/bin/Debug`; rebuild first, then validate

## Validation Checklist

- [x] History prices stay in yuan, for example `4.666 -> 4.666`.
- [x] LEAN daily zip files store scaled integers, for example `46660` instead of `4.666`.
- [x] Strategy blocks zero-price and clearly bad scaling mismatches before sending orders.
- [x] The incorrect `PriceMagnifier=10000` follow-up change was removed.
- [x] Rebuild the solution with a local .NET 10 SDK/runtime.
- [x] Re-run `Launcher/config/config-ashare-etf-backtest.json` and verify fills are quoted in CNY.
- [x] Verify order quantities are in normal lot sizes instead of hundreds of millions of shares.
- [x] Verify the remaining order-invalid issues were a hard-coded price-limit bug, then fix them with symbol-specific and tick-rounded limits.

## Future Enhancements

- `Engine/DataFeeds/Queues/TushareDataQueue.cs` still polls parquet-backed data for local paper/live simulation; real streaming integration remains future work.
- If we extend the ETF universe, add price-limit metadata instead of assuming a universal 10% band.
- If parquet data is refreshed, always rerun `Scripts/convert-tushare-to-lean.py` and treat any new `0` price or `10,000x` mismatch as a storage-format regression first.
- Consider adding an end-to-end regression that compares `History(symbol).Last().Close` with `Securities[symbol].Price` during a short backtest fixture.
- Consider further risk controls (position caps / turnover limits) once strategy design is finalized.

## Lessons Learned

- In LEAN, `equity` daily disk format and in-memory `TradeBar` values are **not** the same thing.
- Always inspect the actual file reader (`Common/Data/Market/TradeBar.cs`) before changing pricing logic.
- Fix the storage format at the source instead of compensating with a second scaling step elsewhere.
- Do not hard-code one market rule for every ETF: growth-board ETFs can use a different price-limit band than main-board ETFs.
- Price-limit validation must respect the instrument minimum price variation; comparing against an unrounded theoretical bound creates false invalid fills.
- Generic equity symbol properties can report a coarser tick than A-share ETFs actually use; do not let that override the ETF tick size.
- Re-running the backtest without rebuilding can make a correct source fix look broken; always validate the compiled DLL timestamps or rebuild first.
- Add validation close to data generation so the same bug cannot silently return.
