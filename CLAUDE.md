# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LEAN is an open-source algorithmic trading engine built in C# with Python support. It's a professional-grade, event-driven platform for backtesting and live trading across multiple asset classes and markets. The codebase is modular and extensible, designed with pluggable components for data feeds, brokerages, execution models, and more.

**Key Technologies:**
- C# (.NET 10.0)
- Python (via Python.NET/pythonnet)
- NodaTime for timezone handling
- Newtonsoft.Json for serialization
- Docker for containerized execution

## Build and Test Commands

### Building the Solution

```bash
# Build entire solution
dotnet build QuantConnect.Lean.sln

# Build specific project
dotnet build Common/QuantConnect.csproj
dotnet build Engine/QuantConnect.Lean.Engine.csproj
```

### Running Tests

```bash
# Run all tests
dotnet test Tests/QuantConnect.Tests.csproj

# Run specific test class
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~ClassName"

# Run tests with detailed output
dotnet test Tests/QuantConnect.Tests.csproj --logger "console;verbosity=detailed"
```

### Running LEAN

```bash
# Run backtest (from repository root)
cd Launcher/bin/Debug
dotnet QuantConnect.Lean.Launcher.dll

# Or from root with project reference
dotnet run --project Launcher

# With custom config
dotnet run --project Launcher --config path/to/config.json
```

### Python Algorithm Development

```bash
# Build Python stubs for IDE autocomplete
./ci_build_stubs.sh

# Run Python algorithm (configure in config.json first)
# Set: "algorithm-language": "Python"
# Set: "algorithm-location": "../../../Algorithm.Python/YourAlgorithm.py"
```

## Architecture Overview

### Layered Architecture

```
Algorithm Layer (User Code)
    ↓
Engine Layer (Core Processing)
    ↓
Data & Execution Layer (Feeds, Brokerages)
    ↓
Market & Security Layer (Models, Exchange)
```

### Core Components

**Engine/** - The heart of LEAN
- `Engine.cs` - Main execution loop for backtesting and live trading
- `DataFeeds/` - Data subscription and streaming (FileSystemDataFeed, LiveTradingDataFeed)
- `HistoricalData/` - History providers for warm-up and research
- `Results/` - Result handling and statistics generation
- `TransactionHandlers/` - Order processing and execution

**Common/** - Shared types and utilities
- `Securities/` - Security models (Equity, Option, Future, Forex, Crypto)
- `Orders/` - Order types (Market, Limit, Stop, StopLimit)
- `Data/` - Data types (TradeBar, Tick, QuoteBar, custom data)
- `Brokerages/` - Brokerage models (fees, fills, slippage, settlement)
- `Symbol.cs` - Universal security identifier

**Algorithm/** - Base algorithm framework
- `QCAlgorithm.cs` - Base class all algorithms inherit from
- Framework modules: Alpha, Portfolio Construction, Execution, Risk Management

**Algorithm.CSharp/** - C# algorithm examples and templates

**Algorithm.Python/** - Python algorithm examples and templates

**Brokerages/** - Brokerage integrations (IB, OANDA, Binance, etc.)

**Indicators/** - Technical indicators library

**ToolBox/** - Data downloading and processing utilities

### Key Design Patterns

**Event-Driven Architecture**: Data flows through the system as time-sliced events
- `TimeSlice` contains all data for a specific moment
- `Slice` provides indexed access to data by Symbol
- Events trigger algorithm callbacks: `OnData()`, `OnOrderEvent()`, `OnSecuritiesChanged()`

**Strategy Pattern**: Pluggable models for different behaviors
- `IFillModel` - How orders get filled
- `IFeeModel` - How fees are calculated
- `ISlippageModel` - How slippage is applied
- `ISettlementModel` - How trades settle (immediate, T+1, T+2)
- `IBuyingPowerModel` - How buying power is calculated

**Factory Pattern**: Component creation through factories
- `BrokerageFactory` - Creates brokerage instances
- `DataFeedFactory` - Creates data feed instances

## Configuration System

Configuration is layered in `Launcher/config.json`:
1. Top-level defaults apply to all environments
2. Environment-specific settings override defaults
3. Environments: "backtesting", "live-paper", "live-interactive"

**Key Configuration Points:**
- `algorithm-type-name` - Which algorithm class to run
- `algorithm-language` - "CSharp" or "Python"
- `algorithm-location` - Path to DLL or .py file
- `data-folder` - Where market data is stored
- `environment` - Which environment configuration to use

## Data Model

### Symbol System

`Symbol` is the universal identifier for securities:
```csharp
Symbol.Create("SPY", SecurityType.Equity, Market.USA)
Symbol.CreateOption("SPY", Market.USA, OptionStyle.American, OptionRight.Call, 450, new DateTime(2024, 12, 20))
```

Components:
- `Value` - Ticker string (e.g., "SPY", "BTCUSD")
- `SecurityType` - Equity, Option, Future, Forex, Crypto, etc.
- `Market` - USA, FXCM, Binance, China (for A-share extension)

### Data Types

**BaseData** - Abstract base for all data
- `Symbol` - What security
- `Time` - When (data timestamp)
- `EndTime` - Period end
- `Value` - Primary value

**TradeBar** - OHLCV bar data
- `Open`, `High`, `Low`, `Close`, `Volume`
- `Period` - Bar duration (1 minute, 1 hour, 1 day)

**Tick** - Tick-level data
- `TickType` - Trade or Quote
- `BidPrice`, `AskPrice`, `Quantity`

**QuoteBar** - Bid/Ask OHLC bars

### Data Resolution

- `Tick` - Every trade/quote
- `Second` - 1-second bars
- `Minute` - 1-minute bars
- `Hour` - 1-hour bars
- `Daily` - Daily bars

## Order System

### Order Flow

```
Algorithm.Order() / SetHoldings() / MarketOrder()
    ↓
TransactionHandler.Process()
    ↓
Brokerage.PlaceOrder()
    ↓
FillModel.Fill() (in backtest) or Real Broker (in live)
    ↓
OrderEvent generated
    ↓
Algorithm.OnOrderEvent()
```

### Order Types

- `MarketOrder` - Execute at current market price
- `LimitOrder` - Execute at specified price or better
- `StopMarketOrder` - Market order triggered at stop price
- `StopLimitOrder` - Limit order triggered at stop price
- `MarketOnOpenOrder` - Execute at market open
- `MarketOnCloseOrder` - Execute at market close

### Order Status Lifecycle

`New` → `Submitted` → `PartiallyFilled` → `Filled`
                    ↘ `Canceled` / `Invalid`

## Python Integration

LEAN uses Python.NET (pythonnet) to enable Python algorithms:
- Python code runs in the same process as C#
- Python algorithms inherit from `QCAlgorithm` (C# base class)
- Full access to LEAN API from Python
- Type conversions handled automatically

**Python Algorithm Structure:**
```python
class MyAlgorithm(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetCash(100000)
        self.AddEquity("SPY", Resolution.Daily)

    def OnData(self, data):
        if not self.Portfolio.Invested:
            self.SetHoldings("SPY", 1.0)
```

## Extension Points

### Adding New Markets (e.g., A-Share Chinese Stocks)

The codebase includes an A-share market extension as a reference (see `00-LEAN-Arch.md`):

1. **Market Definition** - Add to `Market.cs`
2. **Currency** - Add to `Currencies.cs` if needed
3. **Security Type** - Extend `Equity` or create custom security class
4. **Settlement Model** - Implement `ISettlementModel` (e.g., T+1 for A-shares)
5. **Fill Model** - Implement custom fill logic (e.g., price limits, lot sizes)
6. **Fee Model** - Implement `FeeModel` for market-specific fees
7. **Buying Power Model** - Implement `IBuyingPowerModel` for margin rules
8. **Brokerage** - Implement `Brokerage` base class
9. **History Provider** - Extend `HistoryProviderBase`
10. **Data Queue Handler** - Implement `IDataQueueHandler` for live data

### Adding Custom Data

Extend `BaseData` and implement:
- `Reader()` - Parse data from source
- `GetSource()` - Specify data source URL/path
- `Clone()` - Create deep copy

## Testing Patterns

Tests are organized by component in `Tests/` directory:
- `Tests/Algorithm/` - Algorithm behavior tests
- `Tests/Engine/` - Engine component tests
- `Tests/Common/` - Common utilities tests
- `Tests/Brokerages/` - Brokerage integration tests
- `Tests/Indicators/` - Indicator calculation tests

**Test Structure:**
- Use NUnit framework
- Test classes mirror source structure
- Regression tests verify algorithm outputs match expected results

## Common Development Workflows

### Creating a New Algorithm

1. Add new class to `Algorithm.CSharp/` or `Algorithm.Python/`
2. Inherit from `QCAlgorithm`
3. Implement `Initialize()` and `OnData()`
4. Update `config.json` to point to your algorithm
5. Run with `dotnet run --project Launcher`

### Adding a New Indicator

1. Create class in `Indicators/` inheriting from `IndicatorBase<T>`
2. Implement `ComputeNextValue()` method
3. Add tests in `Tests/Indicators/`
4. Optionally add Python wrapper

### Debugging

**C# Debugging:**
- Set `"debugging": true` in config.json
- Set `"debugging-method": "VisualStudio"` or `"LocalCmdline"`
- Attach debugger to Launcher process

**Python Debugging:**
- Set `"debugging-method": "Debugpy"` or `"PyCharm"`
- Configure Python debugger to attach to process

## Important Conventions

### Time Handling

- Use `DateTime` for algorithm time (UTC)
- Use `NodaTime` for timezone conversions
- `Time` property on data is the start of the bar
- `EndTime` is the end of the bar period

### Security Holdings

Access via `Portfolio[symbol]` or `Securities[symbol]`:
- `Holdings.Quantity` - Current position size
- `Holdings.AveragePrice` - Average entry price
- `Holdings.UnrealizedProfit` - Current P&L

### Cash Management

- `Portfolio.Cash` - Available cash
- `Portfolio.TotalPortfolioValue` - Cash + holdings value
- Multi-currency supported via `CashBook`

## Code Style Notes

- Follow Microsoft C# coding conventions
- Use 4-space soft tabs
- All public APIs should have XML documentation comments
- Keep methods focused and under 50 lines when possible
- Prefer immutability - create new objects rather than mutating

## Related Documentation

- Main README: `readme.md`
- Contributing Guide: `CONTRIBUTING.md`
- Architecture Deep Dive: `00-LEAN-Arch.md` (Chinese, covers A-share extension)
- Official Docs: https://www.lean.io/docs/
- ToolBox README: `ToolBox/README.md`
