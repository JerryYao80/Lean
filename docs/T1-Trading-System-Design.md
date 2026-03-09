# A股T+1交易系统设计文档

## 1. 项目概述

### 1.1 背景
当前系统已实现针对T+0 ETF的量化策略回测和live-paper交易。本设计旨在扩展系统，在兼容现有T+0模式的基础上，支持符合T+1规则的A股ETF和个股的量化回测及live-paper交易。

### 1.2 核心目标
1. **兼容性**：完全兼容现有T+0 ETF系统，不破坏已有功能
2. **T+1规则支持**：准确模拟A股T+1交易规则（当日买入次日才能卖出）
3. **统一数据层**：复用tushare数据源，支持ETF和个股
4. **CLI回测模式**：命令行界面用于策略开发、回测、优化
5. **TUI实盘模式**：终端UI界面用于live-paper，适配手机查看
6. **实时数据接入**：集成tushare实时行情数据
7. **交易信号生成**：明确的买卖信号（时间、价格、数量、标的）
8. **资产统计**：实时跟踪总资产、收益率、持仓明细

### 1.3 系统边界
- **IN SCOPE**：
  - T+1 ETF和个股的回测引擎
  - T+1持仓约束（当日买入不可卖出）
  - 涨跌停板限制
  - 手数（100股）约束
  - 费用模型（佣金、印花税、过户费）
  - Tushare数据接入（历史+实时）
  - CLI回测工具
  - TUI live-paper界面
  - 交易信号输出
  - 资产统计和收益分析

- **OUT OF SCOPE**：
  - 券商API对接（用户手动下单）
  - 自动下单执行
  - 期货、期权等衍生品
  - 融资融券
  - 港股通（已有T+0支持，本次不扩展）

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         用户交互层                                │
├─────────────────────────────────────────────────────────────────┤
│  CLI Mode (回测/优化)          │  TUI Mode (Live-Paper)          │
│  - 策略配置                     │  - 实时信号提醒                 │
│  - 参数优化                     │  - 持仓监控                     │
│  - 回测报告                     │  - 资产统计                     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                        策略引擎层                                 │
├─────────────────────────────────────────────────────────────────┤
│  QCAlgorithm (策略基类)                                          │
│  ├─ T+0 Strategy (现有)                                         │
│  └─ T+1 Strategy (新增)                                         │
│     ├─ 持仓约束检查                                              │
│     ├─ 信号生成                                                  │
│     └─ 风险管理                                                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                        交易规则层                                 │
├─────────────────────────────────────────────────────────────────┤
│  Security Models:                                                │
│  ├─ AShareETF (T+0) - ImmediateSettlementModel                 │
│  ├─ AShareETF (T+1) - DelayedSettlementModel (新增)            │
│  └─ AShareStock (T+1) - DelayedSettlementModel (新增)          │
│                                                                  │
│  Trading Models:                                                 │
│  ├─ FeeModel: 佣金+印花税+过户费                                │
│  ├─ FillModel: 涨跌停限制+手数约束                              │
│  ├─ BuyingPowerModel: 可用资金计算                              │
│  └─ SlippageModel: 滑点模型                                     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                        数据层                                     │
├─────────────────────────────────────────────────────────────────┤
│  TushareDataLayer (统一数据接口)                                 │
│  ├─ 历史数据: fund_daily, daily (个股)                          │
│  ├─ 实时数据: TushareDataQueue (WebSocket/轮询)                 │
│  ├─ 基础数据: stock_basic, etf_basic                            │
│  └─ 辅助数据: daily_basic, stk_limit, trade_cal                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Tushare数据源                                 │
│  /home/project/tushare-downloader/tushare_data/                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责

#### 2.2.1 用户交互层
- **CLI Mode**：用于策略开发和回测
  - 参数配置（JSON/命令行）
  - 批量回测和参数优化
  - 生成回测报告（Markdown/JSON）

- **TUI Mode**：用于live-paper实盘
  - 实时信号展示（高亮提醒）
  - 持仓和资产监控
  - 简洁布局（适配手机终端）

#### 2.2.2 策略引擎层
- 继承QCAlgorithm基类
- 实现Initialize()和OnData()
- T+1持仓约束逻辑
- 信号生成和风险控制

#### 2.2.3 交易规则层
- Security类型定义（AShareStock）
- Settlement模型（T+1延迟结算）
- Fee/Fill/BuyingPower模型
- 涨跌停和手数约束

#### 2.2.4 数据层
- TushareDataLayer统一接口
- 历史数据加载
- 实时数据订阅
- 数据缓存和预处理

## 3. 核心设计

### 3.1 T+1交易规则实现

#### 3.1.1 持仓约束
```
当日买入的股票：
- 不能在当日卖出
- 次日开盘后可卖出
- 需要跟踪"可卖数量"和"总持仓数量"

实现方式：
- Portfolio.Holdings.Quantity: 总持仓
- 新增: Portfolio.Holdings.AvailableQuantity: 可卖数量
- 每日开盘前更新: AvailableQuantity += 昨日买入数量
```

#### 3.1.2 Settlement模型
```csharp
// 使用DelayedSettlementModel(1, TimeSpan.FromHours(9))
// T+1: 次日9:00可卖
public class AShareStock : Equity
{
    public AShareStock(...)
    {
        SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));
        FeeModel = new AShareStockFeeModel();
        FillModel = new AShareStockFillModel();
        BuyingPowerModel = new AShareStockBuyingPowerModel();
    }
}
```

### 3.2 数据模型扩展

#### 3.2.1 个股数据结构
```
Tushare数据源:
- daily: ts_code={symbol}/data.parquet (OHLCV)
- daily_basic: 市值、PE、PB等
- stk_limit: 涨跌停价格
- adj_factor: 复权因子
- stock_basic: 股票基本信息

LEAN数据格式:
- Data/equity/sse/daily/{ticker}.zip
- Data/equity/szse/daily/{ticker}.zip
```

#### 3.2.2 数据catalog扩展
```json
{
  "datasets": {
    "daily": {
      "path": "daily/ts_code={symbol}/data.parquet",
      "date_field": "trade_date",
      "symbol_field": "ts_code"
    },
    "stock_basic": {
      "path": "stock_basic/data.parquet",
      "symbol_field": "ts_code"
    },
    "stk_limit": {
      "path": "stk_limit/ts_code={symbol}/data.parquet",
      "date_field": "trade_date",
      "symbol_field": "ts_code"
    }
  }
}
```

### 3.3 费用模型

#### 3.3.1 A股个股费用
```
买入:
- 佣金: 成交金额 × 0.03% (最低5元)
- 过户费(上交所): 成交金额 × 0.002%

卖出:
- 佣金: 成交金额 × 0.03% (最低5元)
- 印花税: 成交金额 × 0.1%
- 过户费(上交所): 成交金额 × 0.002%
```

#### 3.3.2 实现
```csharp
public class AShareStockFeeModel : FeeModel
{
    public decimal CommissionRate { get; set; } = 0.0003m;
    public decimal MinimumCommission { get; set; } = 5m;
    public decimal StampDutyRate { get; set; } = 0.001m; // 仅卖出
    public decimal TransferFeeRate { get; set; } = 0.00002m; // 仅上交所

    public override OrderFee GetOrderFee(OrderFeeParameters parameters)
    {
        var orderValue = Math.Abs(order.Quantity * order.Price);
        var commission = Math.Max(orderValue * CommissionRate, MinimumCommission);

        var stampDuty = 0m;
        if (order.Direction == OrderDirection.Sell)
        {
            stampDuty = orderValue * StampDutyRate;
        }

        var transferFee = 0m;
        if (security.Symbol.ID.Market == Market.SSE)
        {
            transferFee = orderValue * TransferFeeRate;
        }

        return new OrderFee(new CashAmount(
            commission + stampDuty + transferFee,
            Currencies.CNY
        ));
    }
}
```

### 3.4 涨跌停限制

#### 3.4.1 规则
```
主板/中小板: ±10%
创业板/科创板: ±20%
ST股票: ±5%
新股首日: 特殊规则（本期不支持）
```

#### 3.4.2 实现
```csharp
public class AShareStockFillModel : ImmediateFillModel
{
    public override Fill Fill(FillModelParameters parameters)
    {
        var security = parameters.Security;
        var order = parameters.Order;

        // 获取涨跌停价格
        var limitPrices = GetPriceLimits(security, parameters.ConfigProvider);

        // 限价单检查
        if (order is LimitOrder limitOrder)
        {
            if (order.Direction == OrderDirection.Buy &&
                limitOrder.LimitPrice > limitPrices.UpperLimit)
            {
                return new Fill(order) { Status = OrderStatus.Invalid };
            }

            if (order.Direction == OrderDirection.Sell &&
                limitOrder.LimitPrice < limitPrices.LowerLimit)
            {
                return new Fill(order) { Status = OrderStatus.Invalid };
            }
        }

        // 市价单按涨跌停价成交
        var fillPrice = order.Direction == OrderDirection.Buy
            ? Math.Min(security.Price, limitPrices.UpperLimit)
            : Math.Max(security.Price, limitPrices.LowerLimit);

        return new Fill(order)
        {
            FillPrice = fillPrice,
            FillQuantity = order.Quantity,
            Status = OrderStatus.Filled
        };
    }
}
```

## 4. CLI回测模式设计

### 4.1 命令行接口
```bash
# 单次回测
python Scripts/ashare_t1_backtest.py \
  --config config-ashare-t1-backtest.json \
  --start-date 20200101 \
  --end-date 20251231

# 参数优化
python Scripts/ashare_t1_optimize.py \
  --config config-ashare-t1-optimize.json \
  --param-grid params.json \
  --workers 4

# 生成报告
python Scripts/ashare_t1_report.py \
  --backtest-result results/backtest_20250309.json \
  --output reports/
```

### 4.2 配置文件结构
```json
{
  "universe": {
    "type": "static",
    "symbols": ["000001.SZ", "600000.SH"],
    "or": {
      "type": "index_constituents",
      "index": "000300.SH",
      "top_n": 50
    }
  },
  "strategy": {
    "name": "MeanReversionT1",
    "parameters": {
      "lookback_period": 20,
      "entry_threshold": -2.0,
      "exit_threshold": 0.0,
      "max_positions": 10
    }
  },
  "risk_management": {
    "max_position_size": 0.1,
    "stop_loss": -0.05,
    "take_profit": 0.10
  },
  "execution": {
    "order_type": "market",
    "timing": "open"
  },
  "backtest": {
    "start_date": "20200101",
    "end_date": "20251231",
    "initial_capital": 1000000,
    "benchmark": "000300.SH"
  }
}
```

### 4.3 回测报告输出
```markdown
# 回测报告

## 基本信息
- 策略: MeanReversionT1
- 回测区间: 2020-01-01 至 2025-12-31
- 初始资金: 1,000,000 CNY

## 收益指标
- 总收益率: 45.23%
- 年化收益率: 8.12%
- 最大回撤: -15.67%
- 夏普比率: 1.23
- 卡玛比率: 0.52

## 交易统计
- 总交易次数: 1,234
- 胜率: 56.78%
- 平均盈利: 2.34%
- 平均亏损: -1.89%
- 盈亏比: 1.24

## 持仓分析
- 平均持仓天数: 5.6天
- 最大同时持仓: 10只
- 换手率: 234%

## 费用统计
- 总佣金: 12,345 CNY
- 总印花税: 8,901 CNY
- 总过户费: 234 CNY
```

## 5. TUI Live-Paper模式设计

### 5.1 界面布局
```
┌─────────────────────────────────────────────────────────────┐
│ A股T+1量化交易系统 - Live Paper          2025-03-09 14:30:25 │
├─────────────────────────────────────────────────────────────┤
│ 账户总览                                                     │
│ 总资产: ¥1,234,567  可用: ¥456,789  持仓市值: ¥777,778      │
│ 今日收益: +¥12,345 (+1.01%)  累计收益: +¥234,567 (+23.46%) │
├─────────────────────────────────────────────────────────────┤
│ 🔔 交易信号 (2)                                              │
│ [BUY ] 000001.SZ 平安银行  @¥12.34  1000股  09:31:00       │
│ [SELL] 600000.SH 浦发银行  @¥8.56   500股   14:25:00       │
├─────────────────────────────────────────────────────────────┤
│ 持仓明细 (5)                                                 │
│ 代码        名称    数量   成本   现价   盈亏    盈亏率      │
│ 000001.SZ  平安银行  1000  12.10  12.45  +350   +2.89%     │
│ 600000.SH  浦发银行   500   8.90   8.56  -170   -3.82%     │
│ 000002.SZ  万科A     800   15.20  15.80  +480   +3.95%     │
│ ...                                                          │
├─────────────────────────────────────────────────────────────┤
│ 策略状态: 运行中 | 最后更新: 14:30:25 | [Q]退出 [R]刷新     │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 功能特性
1. **实时信号提醒**
   - 新信号高亮显示
   - 声音/震动提醒（可配置）
   - 信号历史记录

2. **持仓监控**
   - 实时盈亏计算
   - 颜色编码（红涨绿跌）
   - 可卖数量提示

3. **资产统计**
   - 总资产、可用资金、持仓市值
   - 今日收益、累计收益
   - 收益率曲线（简化版）

4. **交互操作**
   - Q: 退出
   - R: 手动刷新
   - ↑↓: 滚动持仓列表
   - Enter: 查看详情

### 5.3 实现技术栈
```python
# TUI框架选择
import rich  # 终端美化
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout

# 或使用
import curses  # 标准库，更轻量

# 实时数据更新
import threading
import queue
```

## 6. 实时数据接入

### 6.1 Tushare实时数据
```python
class TushareRealtimeDataFeed:
    """Tushare实时行情数据源"""

    def __init__(self, token: str):
        self.api = ts.pro_api(token)
        self.subscriptions = {}
        self.update_interval = 3  # 秒

    def subscribe(self, symbols: list[str]):
        """订阅实时行情"""
        for symbol in symbols:
            self.subscriptions[symbol] = {
                'last_update': None,
                'data': None
            }

    def get_realtime_quotes(self) -> dict:
        """获取实时行情（轮询模式）"""
        quotes = {}
        for symbol in self.subscriptions:
            df = self.api.realtime_quote(ts_code=symbol)
            if not df.empty:
                quotes[symbol] = {
                    'price': df['price'].iloc[0],
                    'volume': df['volume'].iloc[0],
                    'time': df['time'].iloc[0]
                }
        return quotes
```

### 6.2 数据更新策略
```
交易时段:
- 09:30-11:30, 13:00-15:00
- 更新频率: 3秒

非交易时段:
- 暂停更新
- 显示最后价格

集合竞价:
- 09:15-09:25
- 仅显示，不生成信号
```

## 7. 交易信号生成

### 7.1 信号格式
```json
{
  "signal_id": "20250309_143025_BUY_000001SZ",
  "timestamp": "2025-03-09T14:30:25",
  "action": "BUY",
  "symbol": "000001.SZ",
  "name": "平安银行",
  "price": 12.34,
  "quantity": 1000,
  "reason": "均值回归信号触发",
  "confidence": 0.85,
  "urgency": "normal"
}
```

### 7.2 信号优先级
```
HIGH: 需立即执行（涨停板突破）
NORMAL: 正常执行（常规信号）
LOW: 可选执行（弱信号）
```

### 7.3 信号持久化
```
存储位置: Results/signals/
格式: signals_YYYYMMDD.jsonl (每行一个JSON)
用途:
- 信号历史回溯
- 执行效果分析
- 策略改进依据
```

## 8. 文件组织结构

```
Lean/
├── Common/
│   ├── Securities/
│   │   ├── Equity/
│   │   │   ├── AShareStock.cs (新增)
│   │   │   ├── AShareStockMetadata.cs (新增)
│   │   │   └── AShareETFMetadata.cs (扩展T+1支持)
│   ├── Orders/
│   │   ├── Fees/
│   │   │   └── AShareStockFeeModel.cs (新增)
│   │   └── Fills/
│   │       └── AShareStockFillModel.cs (新增)
│   └── Data/
│       └── Custom/
│           └── AShareStockData.cs (新增)
│
├── Algorithm.CSharp/
│   ├── AShareT1MeanReversionAlgorithm.cs (新增)
│   └── AShareT1MomentumAlgorithm.cs (新增)
│
├── Scripts/
│   ├── ashare_t1_backtest.py (新增)
│   ├── ashare_t1_optimize.py (新增)
│   ├── ashare_t1_live_paper.py (新增)
│   ├── ashare_t1_tui.py (新增)
│   └── tushare_realtime_feed.py (新增)
│
├── Launcher/config/
│   ├── config-ashare-t1-backtest.json (新增)
│   ├── config-ashare-t1-live-paper.json (新增)
│   └── config-ashare-dataset-catalog.json (扩展)
│
└── docs/
    ├── T1-Trading-System-Design.md (本文档)
    ├── T1-Implementation-Guide.md (实现指南)
    ├── T1-Strategy-Development.md (策略开发)
    └── T1-TUI-User-Manual.md (TUI使用手册)
```

## 9. 实施路线图

### Phase 1: 核心基础设施 (2周)
- [ ] AShareStock类实现
- [ ] T+1 Settlement模型
- [ ] Fee/Fill/BuyingPower模型
- [ ] 数据层扩展（个股支持）
- [ ] 单元测试

### Phase 2: 回测引擎 (2周)
- [ ] CLI回测脚本
- [ ] 配置文件解析
- [ ] 回测报告生成
- [ ] 参数优化工具
- [ ] 示例策略实现

### Phase 3: Live-Paper (2周)
- [ ] Tushare实时数据接入
- [ ] TUI界面开发
- [ ] 信号生成和持久化
- [ ] 资产统计模块
- [ ] 手机终端适配

### Phase 4: 测试和优化 (1周)
- [ ] 集成测试
- [ ] 性能优化
- [ ] 文档完善
- [ ] 用户手册

## 10. 风险和注意事项

### 10.1 技术风险
1. **T+1约束实现复杂度**
   - 需要准确跟踪每日买入数量
   - 持仓可卖数量计算
   - 缓解：充分测试，参考现有DelayedSettlementModel

2. **实时数据延迟**
   - Tushare免费版有延迟
   - 缓解：使用付费版或接受延迟

3. **涨跌停板处理**
   - 涨停买不进，跌停卖不出
   - 缓解：信号生成时考虑流动性

### 10.2 业务风险
1. **手动下单延迟**
   - 信号到执行有时间差
   - 缓解：TUI高亮提醒，声音通知

2. **数据质量**
   - Tushare数据可能有误
   - 缓解：数据验证，异常检测

3. **策略过拟合**
   - 回测表现好，实盘差
   - 缓解：样本外测试，walk-forward分析

## 11. 后续扩展方向

1. **策略库扩展**
   - 因子选股策略
   - 行业轮动策略
   - 事件驱动策略

2. **风险管理增强**
   - 组合风险分析
   - VaR计算
   - 压力测试

3. **性能优化**
   - 多进程回测
   - 数据预加载
   - 增量计算

4. **可视化增强**
   - Web界面
   - 图表展示
   - 交互式分析

5. **自动化程度提升**
   - 券商API对接（长期）
   - 自动下单（需合规）
   - 智能止损止盈

---

**文档版本**: v1.0
**创建日期**: 2025-03-09
**作者**: Claude
**状态**: 设计阶段
