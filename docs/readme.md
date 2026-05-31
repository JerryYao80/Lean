# LEAN 量化交易引擎 - 项目总览

## 一、项目简介

LEAN 是一个开源的算法交易引擎，使用 C# (.NET 10.0) 开发，同时支持 Python 策略编写（通过 Python.NET 集成）。本仓库在 LEAN 官方版本基础上，进行了深度的 A 股市场适配，包含完整的 A 股交易规则实现、数据接入（Tushare）和多套 A 股策略。

**核心特性：**
- 事件驱动的回测与实盘模拟（Paper Trading）架构
- 多资产类型支持：股票、ETF、期权、期货、外汇、加密货币、CFD
- Python/C# 双语言策略开发
- 丰富的技术指标库（170+ 文件）
- 25+ 券商/交易所接入
- 模块化的算法框架（Alpha → 组合构建 → 执行 → 风控）
- 完整的 A 股市场适配（T+1、涨跌停、手数、费用）

---

## 二、核心功能

### 2.1 引擎核心（Engine/）

| 组件 | 文件 | 说明 |
|------|------|------|
| **主引擎** | `Engine/Engine.cs` | 加载算法、创建线程、初始化所有处理器，编排算法运行循环 |
| **算法管理器** | `Engine/AlgorithmManager.cs` | 遍历 TimeSlice 时间切片，触发算法回调 |
| **数据源** | `Engine/DataFeeds/` | 回测用 `FileSystemDataFeed`（本地文件），实盘用 `LiveTradingDataFeed` |
| **结果处理器** | `Engine/Results/` | 回测结果生成图表和统计；实盘结果实时流式输出，支持 InfluxDB/Grafana |
| **交易处理器** | `Engine/TransactionHandlers/` | 回测用 `BacktestingTransactionHandler`（模拟成交）；实盘用 `BrokerageTransactionHandler`（真实下单） |
| **历史数据** | `Engine/HistoricalData/` | 从磁盘或券商接口获取历史数据，支持热身 |
| **实时处理** | `Engine/RealTime/` | 定时事件调度（开盘/收盘回调等） |

### 2.2 证券类型（Common/Securities/）

| 类型 | 目录 | 说明 |
|------|------|------|
| **股票 Equity** | `Securities/Equity/` | 包含 `AShareStock`（A 股个股）、`AShareETF`（A 股 ETF） |
| **期权 Option** | `Securities/Option/` | 含策略匹配、希腊字母、QuantLib 定价 |
| **期货 Future** | `Securities/Future/` | 连续合约、到期函数 |
| **外汇 Forex** | `Securities/Forex/` | 货币对交易 |
| **加密货币 Crypto** | `Securities/Crypto/` | 现货与合约 |
| **CFD** | `Securities/Cfd/` | 差价合约 |
| **指数 Index** | `Securities/Index/` | 指数数据 |
| **指数期权** | `Securities/IndexOption/` | 指数期权 |
| **期货期权** | `Securities/FutureOption/` | 期货期权 |

### 2.3 订单系统（Common/Orders/）

支持 **12 种订单类型**：

| 类型 | 说明 |
|------|------|
| `MarketOrder` | 市价单 |
| `LimitOrder` | 限价单 |
| `StopMarketOrder` | 止损市价单 |
| `StopLimitOrder` | 止损限价单 |
| `MarketOnOpenOrder` | 开盘市价单 |
| `MarketOnCloseOrder` | 收盘市价单 |
| `TrailingStopOrder` | 追踪止损单 |
| `LimitIfTouchedOrder` | 触发限价单 |
| `OptionExerciseOrder` | 期权行权 |
| `ComboMarketOrder` | 组合市价单 |
| `ComboLimitOrder` | 组合限价单 |
| `ComboLegLimitOrder` | 组合分腿限价单 |

订单状态生命周期：`New` → `Submitted` → `PartiallyFilled` → `Filled`，或 → `Canceled` / `Invalid`

### 2.4 数据类型

| 类型 | 说明 |
|------|------|
| `Tick` | 逐笔数据（成交/报价） |
| `TradeBar` | OHLCV K 线（开高低收量） |
| `QuoteBar` | 买卖盘 OHLC |
| `RenkoBar` / `VolumeRenkoBar` / `RangeBar` | Renko/量Renko/范围K线 |
| `OptionChain` / `FuturesChain` | 期权/期货合约链 |
| `Dividend` / `Split` / `Delisting` | 公司行动事件 |
| `Slice` | 单一时间步的全市场数据容器 |

数据分辨率：`Tick` → `Second` → `Minute` → `Hour` → `Daily`

### 2.5 技术指标库（Indicators/）

共 170+ 文件，涵盖：

- **移动平均（18 种）**：SMA, EMA, DEMA, TEMA, Hull, KAMA, ALMA, FRAMA 等
- **动量指标**：Momentum, ROC, RSI, Stochastic, Williams%R, CCI, TSI
- **波动率指标**：ATR, Bollinger Bands, Keltner Channels, Donchian Channel
- **趋势指标**：MACD, SAR, SuperTrend, Ichimoku, Aroon
- **成交量指标**：OBV, VWAP, CMF, MFI, Force Index
- **期权希腊字母**：Delta, Gamma, Theta, Vega, Rho, 隐含波动率
- **市场广度**：TRIN, 涨跌比, McClellan 震荡指标
- **K 线形态（64 种）**：Doji, Hammer, Engulfing, MorningStar 等
- **统计指标**：Beta, Correlation, Sharpe, Sortino

### 2.6 算法框架（Algorithm.Framework/）

模块化五层架构：

```
Universe Selection（选股） → Alpha Model（信号） → Portfolio Construction（仓位） → Execution（执行） + Risk Management（风控）
```

**内置模型：**
- **Alpha**：EMA 交叉、MACD、RSI、历史收益、配对交易
- **组合构建**：等权、信心加权、均值方差优化、Black-Litterman、风险平价
- **执行**：立即执行、标准差执行、VWAP 执行
- **风控**：最大回撤限制、未实现利润止盈、行业暴露限制、追踪止损

### 2.7 券商接入（Brokerages/）

支持 25+ 券商/交易所：

| 类别 | 券商 |
|------|------|
| **模拟** | BacktestingBrokerage, PaperBrokerage |
| **美股** | Interactive Brokers, Alpaca, Charles Schwab, Tastytrade, TradeStation, Tradier |
| **期货** | Trading Technologies, Wolverine, RBI, Eze |
| **外汇** | OANDA |
| **加密** | Binance, Bybit, Coinbase, Bitfinex, Kraken, dYdX |
| **印度** | Zerodha, Samco |
| **多资产** | Exante |

### 2.8 工具箱（ToolBox/）

- 数据下载与格式转换
- 合成数据生成器（用于测试）
- Tushare 数据转换器（A 股数据）
- Coarse Universe 生成器
- 因子文件生成器

---

## 三、回测使用方法

### 3.1 基本步骤

1. **准备数据**：将历史行情数据放入 `Data/` 目录（或通过 Tushare 下载转换）
2. **编写策略**：在 `Algorithm.CSharp/` 或 `Algorithm.Python/` 下创建策略类
3. **配置文件**：修改 `Launcher/config.json` 或使用独立配置文件
4. **运行回测**：

```bash
# 方式一：默认配置
cd Launcher/bin/Debug
dotnet QuantConnect.Lean.Launcher.dll

# 方式二：指定配置文件
dotnet QuantConnect.Lean.Launcher.dll --config /path/to/config.json

# 方式三：命令行参数覆盖
dotnet QuantConnect.Lean.Launcher.dll \
  --environment backtesting \
  --algorithm-type-name MyAlgorithm \
  --algorithm-language CSharp \
  --algorithm-location ../../../Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll \
  --data-folder ../../../Data \
  --backtest-name "My Backtest" \
  --results-destination-folder ./output
```

### 3.2 回测配置要点

在 `config.json` 中设置回测环境：

```json
{
  "environment": "backtesting",
  "algorithm-type-name": "BasicTemplateAlgorithm",
  "algorithm-language": "CSharp",
  "algorithm-location": "QuantConnect.Algorithm.CSharp.dll",
  "data-folder": "../../../Data/"
}
```

回测环境使用的处理器：

| 处理器 | 类 | 说明 |
|--------|-----|------|
| 数据源 | `FileSystemDataFeed` | 从本地磁盘读取历史数据 |
| 交易处理 | `BacktestingTransactionHandler` | 使用成交模型模拟订单 |
| 结果处理 | `BacktestingResultHandler` | 生成 JSON 格式的回测报告 |
| 实时处理 | `BacktestingRealTimeHandler` | 模拟时间推进 |

### 3.3 回测结果

输出文件位于 `results-destination-folder` 目录：

| 文件 | 说明 |
|------|------|
| `{algorithm-id}.json` | 完整回测结果（图表、订单、盈亏、统计） |
| `{algorithm-id}-summary.json` | 统计摘要（总收益、夏普比、最大回撤等） |
| `{algorithm-id}-log.txt` | 算法日志 |

### 3.4 Python 策略回测

```bash
dotnet QuantConnect.Lean.Launcher.dll \
  --algorithm-language Python \
  --algorithm-location ../../../Algorithm.Python/MyAlgo.py
```

### 3.5 A 股回测配置示例

使用预置配置文件（位于 `Launcher/config/`）：

```bash
# A 股 ETF 回测
dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-ashare-etf-backtest.json

# A 股 T+1 个股回测
dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-ashare-t1-backtest.json

# A 股 ETF T+0 特征回测
dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-ashare-etf-t0-feature-backtest.json

# A 股 Barra CNE5 因子回测
dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-barra-cne5-pipeline.json
```

可用的 A 股回测配置文件：

| 配置文件 | 策略类型 |
|----------|----------|
| `config-ashare-etf-backtest.json` | ETF 动量 |
| `config-ashare-t1-backtest.json` | T+1 个股 |
| `config-ashare-etf-t0-feature-backtest.json` | ETF T+0 特征 |
| `config-ashare-etf-t0-feature-lean-backtest.json` | ETF T+0 LEAN 格式 |
| `config-ashare-etf-dual-rotation-backtest.json` | ETF 双层轮动 |
| `config-ashare-t1-momentum-lean-backtest.json` | T+1 动量 |
| `config-ashare-llm-quant-backtest.json` | LLM 量化 |
| `config-ashare-llm-quant-lean-backtest.json` | LLM 量化（LEAN 格式） |
| `config-ashare-industry-rotation-lean-backtest.json` | 行业轮动 |
| `config-barra-cne5-pipeline.json` | Barra CNE5 因子 |

---

## 四、Live Paper Trading 使用方法

### 4.1 基本步骤

1. **准备实时数据源**：配置 Tushare 实时数据队列或券商数据接口
2. **配置文件**：使用 `live-paper` 环境或 A 股专用 live-paper 配置
3. **运行**：

```bash
# 通用 Paper Trading
dotnet QuantConnect.Lean.Launcher.dll --environment live-paper

# A 股 ETF Live Paper
dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-ashare-etf-live-paper.json

# A 股 T+1 Live Paper
dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-ashare-t1-live-paper.json
```

### 4.2 Live Paper 配置要点

```json
{
  "environment": "live-paper",
  "live-mode": true,
  "live-mode-brokerage": "PaperBrokerage",
  "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.LiveTradingDataFeed",
  "data-queue-handler": ["TushareDataQueue"],
  "history-provider": ["TushareHistoryProvider"],
  "result-handler": "QuantConnect.Lean.Engine.Results.LiveTradingResultHandler",
  "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler",
  "setup-handler": "QuantConnect.Lean.Engine.Setup.BrokerageSetupHandler",
  "real-time-handler": "QuantConnect.Lean.Engine.RealTime.LiveTradingRealTimeHandler"
}
```

与回测的关键区别：

| 项目 | 回测 | Live Paper |
|------|------|------------|
| 时间推进 | `Synchronizer`（按数据时间） | `LiveSynchronizer`（真实时间） |
| 数据源 | `FileSystemDataFeed`（本地文件） | `LiveTradingDataFeed` + `TushareDataQueue`（实时数据） |
| 券商 | `BacktestingBrokerage` | `PaperBrokerage`（模拟成交） |
| 结果输出 | 写入 JSON 文件 | 实时流式输出 + InfluxDB |
| `live-mode` | `false` | `true` |

### 4.3 Paper Trading 工作流程

```
配置加载 → 创建 PaperBrokerage（模拟券商）
  → 初始化 LiveTradingDataFeed + TushareDataQueue（实时数据）
  → BrokerageSetupHandler 设置实盘状态
  → AlgorithmManager.Run() 实时循环
  → LiveTradingResultHandler 持续输出结果
```

### 4.4 可用的 A 股 Live Paper 配置

| 配置文件 | 策略 |
|----------|------|
| `config-ashare-etf-live-paper.json` | ETF 动量 |
| `config-ashare-t1-live-paper.json` | T+1 个股 |
| `config-ashare-t1-momentum-live-paper.json` | T+1 动量 |
| `config-ashare-etf-dual-rotation-live-paper.json` | ETF 双层轮动 |
| `config-ashare-etf-t0-feature-live-paper.json` | ETF T+0 特征 |
| `config-ashare-llm-quant-live-paper.json` | LLM 量化 |
| `config-ashare-industry-rotation-live-paper.json` | 行业轮动 |
| `config-barra-cne5-live-paper.json` | Barra CNE5 |

### 4.5 监控与可观测性

支持 InfluxDB + Grafana 实时监控：

```json
{
  "influxdb-enabled": true,
  "influxdb-url": "http://127.0.0.1:8086",
  "influxdb-org": "lean",
  "influxdb-bucket": "quant",
  "influxdb-token-env-var": "INFLUXDB_TOKEN"
}
```

### 4.6 Docker 运行

```bash
# 构建
docker build -t lean .

# 运行回测
docker run lean

# 运行自定义配置
docker run lean --config /path/to/config.json
```

---

## 五、A 股适配情况

本项目在 LEAN 引擎上实现了 **全面的 A 股市场适配**，涵盖约 170+ 个 A 股相关文件。

### 5.1 市场定义

| 常量 | 值 | 说明 |
|------|-----|------|
| `Market.China` | `"china"` (43) | 通用中国 A 股市场 |
| `Market.SSE` | `"sse"` (44) | 上海证券交易所 |
| `Market.SZSE` | `"szse"` (45) | 深圳证券交易所 |

货币：`CNY`（人民币），时区：`Asia/Shanghai`

### 5.2 证券类

#### AShareStock（A 股个股，T+1）

文件：`Common/Securities/Equity/AShareStock.cs`

- 继承 `Equity`，配置所有 A 股专用模型
- T+1 结算：`DelayedSettlementModel(1, 09:00)` + `AShareT1Holding` + `AShareT1PortfolioModel`
- 涨跌停限制：主板 10%、创业板/科创板 20%、北交所 30%、ST 股 5%
- 手数：100 股/手，最小价格变动 0.01 元
- 无融资融券（杠杆 1.0，现金账户）
- 禁止买入 ST 股票

#### AShareETF（A 股 ETF，T+0）

文件：`Common/Securities/Equity/AShareETF.cs`

- T+0 即时结算：`ImmediateSettlementModel`
- 涨跌停限制：默认 10%、创业板 ETF 20%
- 手数：100 份/手，最小价格变动 0.001 元
- 内置约 145 只 T+0 ETF 注册表

### 5.3 A 股交易规则适配汇总

| 规则 | 实现 | 文件 |
|------|------|------|
| **T+1 结算**（当日买入不可卖出） | `DelayedSettlementModel` + `AShareT1Holding` | `AShareStock.cs`, `AShareT1Holding.cs` |
| **T+0 结算**（ETF 当日买卖） | `ImmediateSettlementModel` | `AShareETF.cs` |
| **涨跌停**（主板±10%） | `GetUpperPriceLimit()` / `GetLowerPriceLimit()` | `AShareStock.cs`, `AShareStockMetadata.cs` |
| **涨跌停**（创业板/科创板±20%） | 自动识别 300/301/688 开头代码 | `AShareStockMetadata.cs` |
| **涨跌停**（北交所±30%） | 自动识别 43/83/87 开头代码 | `AShareStockMetadata.cs` |
| **涨跌停**（ST 股±5%） | 自动检测 ST/*ST 前缀 | `AShareStockMetadata.cs` |
| **手数**（100 股/手） | `LotSize = 100`，订单数量必须为 100 的整数倍 | `AShareStock.cs`, `AShareETF.cs` |
| **交易时间**（9:30-11:30, 13:00-15:00） | 成交模型中硬编码 | `AShareStockFillModel.cs`, `AShareETFFillModel.cs` |
| **佣金**（0.03%，最低 5 元） | 双向收取 | `AShareStockFeeModel.cs`, `AShareETFFeeModel.cs` |
| **印花税**（0.05%，卖出收取） | 仅个股卖出时收取 | `AShareStockFeeModel.cs` |
| **过户费**（0.001%，双向） | 个股双向收取 | `AShareStockFeeModel.cs` |
| **禁止融资**（现金账户） | 杠杆率 1.0 | `AShareStockBuyingPowerModel.cs` |
| **禁止买入 ST** | 成交模型和购买力模型双重拦截 | `AShareStockFillModel.cs`, `AShareStockBuyingPowerModel.cs` |
| **涨停无法买入**（无流动性） | 市价买单在涨停价时被拒 | `AShareStockFillModel.cs` |
| **跌停无法卖出**（无流动性） | 市价卖单在跌停价时被拒 | `AShareStockFillModel.cs` |

### 5.4 费用结构

#### A 股个股费用（AShareStockFeeModel）

| 费用项 | 费率 | 最低 | 方向 |
|--------|------|------|------|
| 佣金 | 0.03% | 5 元 | 双向 |
| 印花税 | 0.05% | - | 仅卖出 |
| 过户费 | 0.001% | - | 双向 |

#### A 股 ETF 费用（AShareETFFeeModel）

| 费用项 | 费率 | 最低 | 方向 |
|--------|------|------|------|
| 佣金 | 0.03% | 5 元 | 双向 |
| 过户费 | 0.002% | - | 仅上交所 ETF |

ETF 免印花税。

### 5.5 数据接入（Tushare）

| 组件 | 文件 | 说明 |
|------|------|------|
| 数据转换器 | `Common/Data/TushareDataConverter.cs` | Tushare Parquet → LEAN 格式 |
| 实时数据队列 | `Engine/DataFeeds/Queues/TushareDataQueue.cs` | 实盘 Paper Trading 数据源 |
| 历史数据提供 | `Engine/HistoricalData/TushareHistoryProvider.cs` | 历史数据查询 |
| 数据缓存 | `Common/Data/TushareDataCache.cs` | ETF 元数据缓存 |
| 自定义数据类型 | `Common/Data/Custom/AShareStockData.cs` | A 股日线数据 |
| 数据下载工具 | `data-source/tushare/` | ~40 个 Python 文件，含实时/增量下载 |

### 5.6 A 股策略实现

| 策略 | 文件 | 说明 |
|------|------|------|
| T+1 动量 | `Algorithm.CSharp/AShareT1MomentumAlgorithm.cs` | 个股动量策略 |
| T+1 均值回归 | `Algorithm.CSharp/AShareT1MeanReversionAlgorithm.cs` | 均值回归策略 |
| ETF 双层轮动 | `Algorithm.CSharp/AShareEtfDualRotationPlanAlgorithm.cs` | T+0/T+1 ETF 轮动 |
| ETF T+0 特征 | `Algorithm.CSharp/AShareEtfT0FeatureIntradayAlgorithm.cs` | T+0 ETF 日内策略 |
| 行业轮动 | `Algorithm.CSharp/AShareIndustryRotationPlanAlgorithm.cs` | 行业轮动策略 |
| Barra CNE5 因子 | `Algorithm.CSharp/AShareBarraCNE5Algorithm.cs` | Barra 多因子选股 |
| LLM 量化 | `Algorithm.CSharp/AShareLlmQuantLeanAlgorithm.cs` | 大模型辅助量化 |

### 5.7 A 股适配完整度评估

| 要求 | 状态 | 说明 |
|------|------|------|
| T+1 结算制度 | ✅ 完整 | 含持仓可用性追踪 |
| 涨跌停板制度 | ✅ 完整 | 主板/创业板/科创板/北交所/ST 分级 |
| 手数规则（100 股/手） | ✅ 完整 | 订单和购买力模型双重验证 |
| A 股费用结构 | ✅ 完整 | 佣金/印花税/过户费 |
| 交易时间 | ✅ 完整 | 9:30-11:30, 13:00-15:00 |
| ST 股处理 | ✅ 完整 | 检测并禁止买入 |
| 现金账户（无融资） | ✅ 完整 | 杠杆 1.0 |
| T+0 ETF 交易 | ✅ 完整 | 145+ 只 T+0 ETF 注册 |
| 数据接入 | ✅ 完整 | Tushare 历史数据 + 实时数据 |
| 集合竞价 | ❌ 未实现 | 未找到集合竞价模拟 |
| 注册制 IPO 规则 | ❌ 未实现 | 无特殊处理 |
| 融资融券 | ❌ 未实现 | 仅现金账户 |
| 沪港通/深港通 | ❌ 未实现 | 无港股通支持 |

---

## 六、标准策略结构

### 6.1 最简策略模板（C#）

```csharp
using QuantConnect.Data;

namespace QuantConnect.Algorithm.CSharp
{
    public class BasicTemplateAlgorithm : QCAlgorithm
    {
        private Symbol _spy;

        public override void Initialize()
        {
            SetStartDate(2013, 10, 7);     // 回测起始日期
            SetEndDate(2013, 10, 11);       // 回测结束日期
            SetCash(100000);                // 初始资金
            _spy = AddEquity("SPY", Resolution.Minute).Symbol;  // 订阅数据
        }

        public override void OnData(Slice slice)
        {
            if (!Portfolio.Invested)
            {
                SetHoldings(_spy, 1);       // 满仓买入
            }
        }
    }
}
```

### 6.2 最简策略模板（Python）

```python
from AlgorithmImports import *

class BasicTemplateAlgorithm(QCAlgorithm):
    def initialize(self):
        self.set_start_date(2013, 10, 7)
        self.set_end_date(2013, 10, 11)
        self.set_cash(100000)
        self.symbol = self.add_equity("SPY", Resolution.MINUTE).symbol

    def on_data(self, data):
        if not self.portfolio.invested:
            self.set_holdings(self.symbol, 1)
```

### 6.3 一个标准策略的组成要素

#### 1. Initialize() — 初始化（必须重写）

这是唯一必须重写的方法，在算法启动时调用一次：

```csharp
public override void Initialize()
{
    // 时间范围
    SetStartDate(2020, 1, 1);
    SetEndDate(2023, 12, 31);
    SetCash(100000);

    // 订阅数据
    var spy = AddEquity("SPY", Resolution.Daily);
    var eurusd = AddForex("EURUSD", Resolution.Minute);
    var btcusd = AddCrypto("BTCUSD", Resolution.Minute);
    var es = AddFuture(Futures.Indices.SP500EMini);
    var option = AddOption("SPY");

    // 创建指标
    _ema = EMA("SPY", 20, Resolution.Daily);
    _rsi = RSI("SPY", 14, MovingAverageType.Wilders, Resolution.Daily);
    _macd = MACD("SPY", 12, 26, 9, MovingAverageType.Exponential, Resolution.Daily);

    // 设置热身期
    SetWarmup(20);                              // 按默认分辨率热身 20 根 Bar
    SetWarmup(200, Resolution.Daily);           // 按日线热身 200 根

    // 经纪商模型
    SetBrokerageModel(BrokerageName.InteractiveBrokers);
}
```

#### 2. OnData() — 数据处理（核心回调）

每个时间步触发一次：

```csharp
public override void OnData(Slice slice)
{
    if (IsWarmingUp) return;                    // 热身期间不交易

    // 访问数据
    if (slice.ContainsKey(_symbol))
    {
        var price = slice[_symbol].Close;
    }

    // 使用指标
    if (_ema.IsReady && _rsi.IsReady)
    {
        if (_rsi > 70 && Portfolio[_symbol].Invested)
            Liquidate(_symbol);                 // 平仓
        else if (_rsi < 30 && !Portfolio[_symbol].Invested)
            SetHoldings(_symbol, 0.5);          // 半仓买入
    }
}
```

#### 3. 其他可选回调

| 方法 | 说明 |
|------|------|
| `OnOrderEvent(OrderEvent)` | 订单成交事件 |
| `OnSecuritiesChanged(SecurityChanges)` | Universe 变动（新增/移除证券） |
| `OnEndOfDay()` | 每日收盘 |
| `OnEndOfAlgorithm()` | 算法结束 |
| `OnDividends(Dividends)` | 分红事件 |
| `OnSplits(Splits)` | 拆股事件 |
| `OnDelistings(Delistings)` | 退市事件 |
| `OnBrokerageMessage(BrokerageMessageEvent)` | 券商消息 |
| `OnMarginCallWarning()` | 保证金预警 |
| `OnWarmupFinished()` | 热身完成 |

#### 4. 订单方法

```csharp
MarketOrder(symbol, 100);                          // 市价买 100 股
MarketOrder(symbol, -100);                         // 市价卖 100 股
Buy(symbol, 100);                                  // 买入
Sell(symbol, 100);                                 // 卖出
LimitOrder(symbol, 100, 150.00m);                  // 限价 150 买入 100 股
StopMarketOrder(symbol, -100, 140.00m);            // 跌破 140 止损卖出
StopLimitOrder(symbol, -100, 140.00m, 139.50m);    // 跌破 140 以 139.5 限价卖出
SetHoldings(symbol, 0.5);                          // 目标仓位 50%
SetHoldings(symbol, -0.2);                         // 目标空头 20%
Liquidate();                                       // 清仓所有
Liquidate(symbol);                                 // 清仓指定品种
```

#### 5. 定时事件

```csharp
Schedule.On(DateRules.EveryDay("SPY"), TimeRules.BeforeMarketClose("SPY", 10), () =>
{
    Log("收盘前 10 分钟执行");
});

Schedule.On(DateRules.MonthStart("SPY"), TimeRules.AfterMarketOpen("SPY"), () =>
{
    Log("每月开盘执行调仓");
});
```

#### 6. 历史数据查询

```csharp
// 最近 30 天日线
var history = History<TradeBar>(_symbol, 30, Resolution.Daily);
foreach (var bar in history)
{
    Log($"{bar.Time}: Close={bar.Close}");
}

// 指定日期范围
var history = History(symbols, startDate, endDate, Resolution.Daily);
```

### 6.4 A 股策略模板

```csharp
public class AShareStrategy : QCAlgorithm
{
    private const int LotSize = 100;
    private Symbol _symbol;

    public override void Initialize()
    {
        SetAccountCurrency(Currencies.CNY);         // 人民币
        SetTimeZone(TimeZones.Shanghai);             // 上海时区
        SetStartDate(2020, 1, 1);
        SetEndDate(2023, 12, 31);
        SetCash(1000000);

        _symbol = AddEquity("600519", Resolution.Daily, Market.SSE).Symbol;

        // 设置 A 股专用模型
        var security = Securities[_symbol];
        security.FeeModel = new AShareStockFeeModel();
        security.FillModel = new AShareStockFillModel();
        security.BuyingPowerModel = new AShareStockBuyingPowerModel();
        security.SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));
    }

    public override void OnData(Slice slice)
    {
        if (IsWarmingUp) return;
        if (!slice.ContainsKey(_symbol)) return;

        var price = slice[_symbol].Close;
        var holdings = Portfolio[_symbol];

        // 买入：确保数量为 100 的整数倍
        if (!holdings.Invested)
        {
            var quantity = (int)(Portfolio.Cash / price / LotSize) * LotSize;
            if (quantity > 0)
                MarketOrder(_symbol, quantity);
        }

        // 卖出：T+1 限制，只能卖出昨日之前买入的股票
        if (holdings.Invested && price > holdings.AveragePrice * 1.10m)
        {
            var sellable = ((AShareT1Holding)holdings).AvailableQuantity;
            if (sellable > 0)
                MarketOrder(_symbol, -sellable);
        }
    }
}
```

### 6.5 框架式策略模板

```csharp
public class FrameworkStrategy : QCAlgorithm
{
    public override void Initialize()
    {
        SetStartDate(2020, 1, 1);
        SetEndDate(2023, 12, 31);
        SetCash(100000);

        // 1. 选股模型
        SetUniverseSelection(new ManualUniverseSelectionModel(
            QuantConnect.Symbol.Create("SPY", SecurityType.Equity, Market.USA)));

        // 2. Alpha 模型（产生交易信号）
        SetAlpha(new EmaCrossAlphaModel());

        // 3. 组合构建模型（将信号转化为目标仓位）
        SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());

        // 4. 执行模型（如何下单）
        SetExecution(new ImmediateExecutionModel());

        // 5. 风控模型（风险限制）
        SetRiskManagement(new MaximumDrawdownPercentPerSecurity(0.05m));
    }
}
```

---

## 七、构建与测试

### 构建

```bash
dotnet build QuantConnect.Lean.sln
```

### 运行测试

```bash
# 全部测试
dotnet test Tests/QuantConnect.Tests.csproj

# 特定测试类
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~AShareStockTests"
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~AShareStockFeeModelTests"
```

### A 股相关测试

| 测试文件 | 覆盖内容 |
|----------|----------|
| `Tests/Common/Securities/AShareStockTests.cs` | 价格限制、T+1 持仓、结算 |
| `Tests/Common/Securities/AShareStockBuyingPowerModelTests.cs` | 手数、ST 阻断、T+1 可卖 |
| `Tests/Common/Securities/AShareETFTests.cs` | ETF 价格限制、手数、tick |
| `Tests/Common/Orders/Fees/AShareStockFeeModelTests.cs` | 佣金/印花税/过户费 |
| `Tests/Common/Orders/Fills/AShareStockFillModelTests.cs` | 成交验证、手数、涨跌停 |
| `Tests/Common/Data/Custom/AShareStockDataTests.cs` | 自定义数据解析 |

---

## 八、项目目录结构

```
Lean/
├── Algorithm/              # QCAlgorithm 基类 + 框架接口
├── Algorithm.CSharp/       # C# 策略示例（含 A 股策略）
├── Algorithm.Python/       # Python 策略示例（400+ 文件）
├── Algorithm.Framework/    # 框架模型（Alpha, Portfolio, Execution, Risk）
├── Brokerages/             # 券商接入（25+ 券商）
├── Common/                 # 核心类型：证券、订单、数据、费用
├── Compression/            # 数据压缩/解压
├── Configuration/          # 配置系统
├── Data/                   # 行情数据目录
├── data-source/            # Tushare 数据下载工具
├── Engine/                 # 引擎核心：数据源、结果、交易、历史
├── Indicators/             # 技术指标库（170+ 文件）
├── Launcher/               # 启动器入口 + 配置文件
│   └── config/             # A 股专用配置文件（27+ 个）
├── Logging/                # 日志系统
├── Optimizer/              # 参数优化器
├── Report/                 # 回测报告生成
├── Research/               # 研究环境
├── Tests/                  # 测试套件
├── ToolBox/                # 数据工具箱
├── docs/                   # 文档
└── Scripts/                # A 股运维脚本（20+ 个 Python 脚本）
```
