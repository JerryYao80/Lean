# A股T+1交易系统实现指南

## 1. 实现优先级

### 1.1 核心组件（必须实现）

#### Priority 1: 基础设施
1. **AShareStock类** (`Common/Securities/Equity/AShareStock.cs`)
2. **AShareStockFeeModel** (`Common/Orders/Fees/AShareStockFeeModel.cs`)
3. **AShareStockFillModel** (`Common/Orders/Fills/AShareStockFillModel.cs`)
4. **AShareStockBuyingPowerModel** (`Common/Securities/AShareStockBuyingPowerModel.cs`)

#### Priority 2: 数据层
1. **数据catalog扩展** (支持个股daily数据)
2. **数据导出脚本** (Tushare → LEAN格式)
3. **股票元数据注册表** (`AShareStockMetadata.cs`)

#### Priority 3: 回测引擎
1. **CLI回测脚本** (`Scripts/ashare_t1_backtest.py`)
2. **示例策略** (`Algorithm.CSharp/AShareT1MeanReversionAlgorithm.cs`)
3. **配置文件** (`config-ashare-t1-backtest.json`)

#### Priority 4: Live-Paper
1. **实时数据接入** (`Scripts/tushare_realtime_feed.py`)
2. **TUI界面** (`Scripts/ashare_t1_tui.py`)
3. **信号生成和持久化**

## 2. 详细实现步骤

### 2.1 AShareStock类实现

```csharp
// Common/Securities/Equity/AShareStock.cs
using System;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Orders.Slippage;

namespace QuantConnect.Securities.Equity
{
    /// <summary>
    /// A-Share individual stock with T+1 trading rules
    /// </summary>
    public class AShareStock : Equity
    {
        public const decimal DefaultPriceLimitPercentage = 0.10m;
        public const decimal STStockPriceLimitPercentage = 0.05m;
        public const decimal GrowthBoardPriceLimitPercentage = 0.20m;
        public const decimal DefaultMinimumPriceVariation = 0.01m;
        public const int LotSize = 100;

        public AShareStock(
            Symbol symbol,
            SecurityExchangeHours exchangeHours,
            Cash quoteCurrency,
            SymbolProperties symbolProperties,
            ICurrencyConverter currencyConverter,
            IRegisteredSecurityDataTypesProvider registeredTypes,
            SecurityCache cache)
            : base(symbol, exchangeHours, quoteCurrency, symbolProperties,
                   currencyConverter, registeredTypes, cache)
        {
            // T+1 settlement: next day at 9:00 AM
            SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));

            FeeModel = new AShareStockFeeModel();
            FillModel = new AShareStockFillModel();
            BuyingPowerModel = new AShareStockBuyingPowerModel();
            SlippageModel = new ConstantSlippageModel(0);
        }

        public static decimal GetPriceLimitPercentage(Symbol symbol)
        {
            var ticker = symbol?.Value;
            if (string.IsNullOrEmpty(ticker))
                return DefaultPriceLimitPercentage;

            // ST股票
            if (ticker.StartsWith("ST", StringComparison.OrdinalIgnoreCase))
                return STStockPriceLimitPercentage;

            // 科创板 (688xxx)
            if (ticker.StartsWith("688"))
                return GrowthBoardPriceLimitPercentage;

            // 创业板 (300xxx)
            if (ticker.StartsWith("300"))
                return GrowthBoardPriceLimitPercentage;

            return DefaultPriceLimitPercentage;
        }

        public static bool IsValidQuantity(decimal quantity)
        {
            return quantity % LotSize == 0;
        }
    }
}
```

### 2.2 费用模型实现

```csharp
// Common/Orders/Fees/AShareStockFeeModel.cs
using System;
using QuantConnect.Orders;
using QuantConnect.Securities;

namespace QuantConnect.Orders.Fees
{
    public class AShareStockFeeModel : FeeModel
    {
        public decimal CommissionRate { get; set; } = 0.0003m;
        public decimal MinimumCommission { get; set; } = 5m;
        public decimal StampDutyRate { get; set; } = 0.001m;
        public decimal TransferFeeRate { get; set; } = 0.00002m;

        public override OrderFee GetOrderFee(OrderFeeParameters parameters)
        {
            var order = parameters.Order;
            var security = parameters.Security;

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

            var totalFee = commission + stampDuty + transferFee;
            return new OrderFee(new CashAmount(totalFee, Currencies.CNY));
        }
    }
}
```

### 2.3 Fill模型实现

```csharp
// Common/Orders/Fills/AShareStockFillModel.cs
using System;
using QuantConnect.Data;
using QuantConnect.Orders;
using QuantConnect.Securities;

namespace QuantConnect.Orders.Fills
{
    public class AShareStockFillModel : ImmediateFillModel
    {
        public override OrderEvent Fill(FillModelParameters parameters)
        {
            var order = parameters.Order;
            var security = parameters.Security;
            var configProvider = parameters.ConfigProvider;

            // 获取涨跌停价格
            var limits = GetPriceLimits(security, configProvider);
            if (limits == null)
            {
                return base.Fill(parameters);
            }

            // 检查限价单是否超出涨跌停
            if (order is LimitOrder limitOrder)
            {
                if (order.Direction == OrderDirection.Buy &&
                    limitOrder.LimitPrice > limits.UpperLimit)
                {
                    return new OrderEvent(order, security.LocalTime, OrderFee.Zero)
                    {
                        Status = OrderStatus.Invalid,
                        Message = $"Buy limit price {limitOrder.LimitPrice} exceeds upper limit {limits.UpperLimit}"
                    };
                }

                if (order.Direction == OrderDirection.Sell &&
                    limitOrder.LimitPrice < limits.LowerLimit)
                {
                    return new OrderEvent(order, security.LocalTime, OrderFee.Zero)
                    {
                        Status = OrderStatus.Invalid,
                        Message = $"Sell limit price {limitOrder.LimitPrice} below lower limit {limits.LowerLimit}"
                    };
                }
            }

            // 市价单按涨跌停限制成交
            var fillPrice = security.Price;
            if (order.Direction == OrderDirection.Buy)
            {
                fillPrice = Math.Min(fillPrice, limits.UpperLimit);
            }
            else
            {
                fillPrice = Math.Max(fillPrice, limits.LowerLimit);
            }

            return new OrderEvent(order, security.LocalTime, OrderFee.Zero)
            {
                FillPrice = fillPrice,
                FillQuantity = order.Quantity,
                Status = OrderStatus.Filled
            };
        }

        private PriceLimits GetPriceLimits(Security security, IConfigProvider configProvider)
        {
            // 从配置或数据中获取前收盘价
            var previousClose = GetPreviousClose(security);
            if (previousClose <= 0)
                return null;

            var limitPercentage = AShareStock.GetPriceLimitPercentage(security.Symbol);
            var minPriceVariation = AShareStock.DefaultMinimumPriceVariation;

            return new PriceLimits
            {
                UpperLimit = RoundPrice(previousClose * (1 + limitPercentage), minPriceVariation),
                LowerLimit = RoundPrice(previousClose * (1 - limitPercentage), minPriceVariation)
            };
        }

        private decimal GetPreviousClose(Security security)
        {
            // 从缓存中获取前收盘价
            var history = security.Cache.GetAll<TradeBar>();
            if (history.Count > 0)
            {
                return history[history.Count - 1].Close;
            }
            return security.Close;
        }

        private decimal RoundPrice(decimal price, decimal minPriceVariation)
        {
            return Math.Round(price / minPriceVariation, 0, MidpointRounding.AwayFromZero)
                   * minPriceVariation;
        }

        private class PriceLimits
        {
            public decimal UpperLimit { get; set; }
            public decimal LowerLimit { get; set; }
        }
    }
}
```

### 2.4 数据导出脚本

```python
# Scripts/export_ashare_stock_data.py
import json
import zipfile
from pathlib import Path
import pandas as pd
from tushare_data_layer import TushareDataLayer

def export_stock_to_lean(
    ts_code: str,
    tushare_data_path: Path,
    lean_data_path: Path,
    start_date: str,
    end_date: str
) -> bool:
    """导出单只股票数据到LEAN格式"""

    layer = TushareDataLayer(tushare_data_path)

    # 加载日线数据
    df = layer.load_dataset(
        'daily',
        symbol=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields=['trade_date', 'open', 'high', 'low', 'close', 'vol']
    )

    if df.empty:
        return False

    # 转换为LEAN格式
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df = df.sort_values('trade_date')

    # 价格放大10000倍，成交量放大100倍
    rows = []
    for _, row in df.iterrows():
        rows.append(
            f"{row['trade_date'].strftime('%Y%m%d')} 00:00,"
            f"{int(row['open'] * 10000)},"
            f"{int(row['high'] * 10000)},"
            f"{int(row['low'] * 10000)},"
            f"{int(row['close'] * 10000)},"
            f"{int(row['vol'] * 100)}"
        )

    # 确定市场和路径
    ticker, suffix = ts_code.split('.')
    market = 'sse' if suffix == 'SH' else 'szse'

    output_dir = lean_data_path / 'equity' / market / 'daily'
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / f"{ticker}.csv"
    zip_path = output_dir / f"{ticker}.zip"

    # 写入CSV
    csv_content = "Date,Open,High,Low,Close,Volume\n" + "\n".join(rows)
    csv_path.write_text(csv_content, encoding='utf-8')

    # 创建ZIP
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{ticker.lower()}.csv", "\n".join(rows))

    return True

def export_universe(config_path: str):
    """批量导出股票池"""
    config = json.loads(Path(config_path).read_text())

    tushare_data_path = Path(config['tushare-data-path'])
    lean_data_path = Path(config['lean-data-path'])
    start_date = config['start-date']
    end_date = config['end-date']

    # 从stock_basic获取股票列表
    layer = TushareDataLayer(tushare_data_path)
    stock_basic = layer.load_dataset('stock_basic')

    # 过滤：仅A股，排除退市
    stocks = stock_basic[
        (stock_basic['market'] == 'A股') &
        (stock_basic['list_status'] == 'L')
    ]['ts_code'].tolist()

    print(f"Total stocks to export: {len(stocks)}")

    success_count = 0
    for i, ts_code in enumerate(stocks, 1):
        if export_stock_to_lean(ts_code, tushare_data_path, lean_data_path,
                                start_date, end_date):
            success_count += 1

        if i % 100 == 0:
            print(f"Progress: {i}/{len(stocks)} ({success_count} succeeded)")

    print(f"Export completed: {success_count}/{len(stocks)} succeeded")

if __name__ == '__main__':
    export_universe('config-ashare-stock-export.json')
```

### 2.5 回测脚本

```python
# Scripts/ashare_t1_backtest.py
import argparse
import json
from pathlib import Path
import subprocess

def run_backtest(config_path: str):
    """运行T+1回测"""
    config = json.loads(Path(config_path).read_text())

    # 构建LEAN命令
    cmd = [
        'dotnet', 'run',
        '--project', 'Launcher',
        '--',
        '--config', config_path,
        '--environment', 'backtesting'
    ]

    print(f"Running backtest with config: {config_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print("Backtest completed successfully")
        print(result.stdout)
    else:
        print("Backtest failed")
        print(result.stderr)

    return result.returncode

def main():
    parser = argparse.ArgumentParser(description='A-Share T+1 Backtest')
    parser.add_argument('--config', required=True, help='Config file path')
    parser.add_argument('--start-date', help='Override start date (YYYYMMDD)')
    parser.add_argument('--end-date', help='Override end date (YYYYMMDD)')

    args = parser.parse_args()

    # 加载配置
    config = json.loads(Path(args.config).read_text())

    # 覆盖参数
    if args.start_date:
        config['backtest']['start_date'] = args.start_date
    if args.end_date:
        config['backtest']['end_date'] = args.end_date

    # 保存临时配置
    temp_config = Path('temp_backtest_config.json')
    temp_config.write_text(json.dumps(config, indent=2))

    # 运行回测
    return_code = run_backtest(str(temp_config))

    # 清理
    temp_config.unlink()

    return return_code

if __name__ == '__main__':
    exit(main())
```

### 2.6 TUI界面实现

```python
# Scripts/ashare_t1_tui.py
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
import time
from datetime import datetime
from tushare_realtime_feed import TushareRealtimeDataFeed

class AShareT1TUI:
    def __init__(self, config_path: str):
        self.console = Console()
        self.config = self.load_config(config_path)
        self.data_feed = TushareRealtimeDataFeed(self.config['tushare_token'])
        self.portfolio = self.load_portfolio()
        self.signals = []

    def load_config(self, config_path: str) -> dict:
        import json
        from pathlib import Path
        return json.loads(Path(config_path).read_text())

    def load_portfolio(self) -> dict:
        """加载持仓数据"""
        return {
            'cash': 456789.0,
            'total_value': 1234567.0,
            'positions': [
                {
                    'symbol': '000001.SZ',
                    'name': '平安银行',
                    'quantity': 1000,
                    'cost': 12.10,
                    'price': 12.45,
                    'available': 1000
                },
                {
                    'symbol': '600000.SH',
                    'name': '浦发银行',
                    'quantity': 500,
                    'cost': 8.90,
                    'price': 8.56,
                    'available': 0  # 今日买入，不可卖
                }
            ]
        }

    def create_layout(self) -> Layout:
        """创建界面布局"""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="account", size=4),
            Layout(name="signals", size=6),
            Layout(name="positions"),
            Layout(name="footer", size=1)
        )
        return layout

    def render_header(self) -> Panel:
        """渲染标题栏"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        text = Text()
        text.append("A股T+1量化交易系统 - Live Paper", style="bold cyan")
        text.append(f"          {now}", style="dim")
        return Panel(text, border_style="cyan")

    def render_account(self) -> Panel:
        """渲染账户总览"""
        p = self.portfolio
        total = p['total_value']
        cash = p['cash']
        market_value = total - cash

        today_pnl = 12345.0
        today_pnl_pct = 1.01
        total_pnl = 234567.0
        total_pnl_pct = 23.46

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(style="cyan")
        table.add_column(style="white")

        table.add_row("总资产:", f"¥{total:,.0f}")
        table.add_row("可用资金:", f"¥{cash:,.0f}")
        table.add_row("持仓市值:", f"¥{market_value:,.0f}")
        table.add_row(
            "今日收益:",
            f"[green]+¥{today_pnl:,.0f} (+{today_pnl_pct:.2f}%)[/green]"
        )
        table.add_row(
            "累计收益:",
            f"[green]+¥{total_pnl:,.0f} (+{total_pnl_pct:.2f}%)[/green]"
        )

        return Panel(table, title="账户总览", border_style="green")

    def render_signals(self) -> Panel:
        """渲染交易信号"""
        table = Table(show_header=True, box=None)
        table.add_column("操作", style="bold")
        table.add_column("代码")
        table.add_column("名称")
        table.add_column("价格", justify="right")
        table.add_column("数量", justify="right")
        table.add_column("时间")

        # 示例信号
        signals = [
            ("BUY", "000001.SZ", "平安银行", 12.34, 1000, "09:31:00"),
            ("SELL", "600000.SH", "浦发银行", 8.56, 500, "14:25:00")
        ]

        for action, symbol, name, price, qty, time in signals:
            action_style = "green" if action == "BUY" else "red"
            table.add_row(
                f"[{action_style}]{action}[/{action_style}]",
                symbol, name,
                f"¥{price:.2f}",
                f"{qty}",
                time
            )

        title = f"🔔 交易信号 ({len(signals)})"
        return Panel(table, title=title, border_style="yellow")

    def render_positions(self) -> Panel:
        """渲染持仓明细"""
        table = Table(show_header=True)
        table.add_column("代码")
        table.add_column("名称")
        table.add_column("数量", justify="right")
        table.add_column("可卖", justify="right")
        table.add_column("成本", justify="right")
        table.add_column("现价", justify="right")
        table.add_column("盈亏", justify="right")
        table.add_column("盈亏率", justify="right")

        for pos in self.portfolio['positions']:
            pnl = (pos['price'] - pos['cost']) * pos['quantity']
            pnl_pct = (pos['price'] / pos['cost'] - 1) * 100

            pnl_style = "green" if pnl >= 0 else "red"
            pnl_sign = "+" if pnl >= 0 else ""

            available_text = str(pos['available'])
            if pos['available'] == 0:
                available_text = "[dim]0 (T+1)[/dim]"

            table.add_row(
                pos['symbol'],
                pos['name'],
                str(pos['quantity']),
                available_text,
                f"¥{pos['cost']:.2f}",
                f"¥{pos['price']:.2f}",
                f"[{pnl_style}]{pnl_sign}¥{pnl:.0f}[/{pnl_style}]",
                f"[{pnl_style}]{pnl_sign}{pnl_pct:.2f}%[/{pnl_style}]"
            )

        return Panel(table, title="持仓明细", border_style="blue")

    def render_footer(self) -> Panel:
        """渲染底部状态栏"""
        text = Text()
        text.append("策略状态: ", style="dim")
        text.append("运行中", style="green")
        text.append(" | 最后更新: ", style="dim")
        text.append(datetime.now().strftime('%H:%M:%S'), style="cyan")
        text.append(" | [Q]退出 [R]刷新", style="dim")
        return Panel(text, border_style="dim")

    def run(self):
        """运行TUI"""
        layout = self.create_layout()

        with Live(layout, console=self.console, refresh_per_second=1) as live:
            while True:
                layout["header"].update(self.render_header())
                layout["account"].update(self.render_account())
                layout["signals"].update(self.render_signals())
                layout["positions"].update(self.render_positions())
                layout["footer"].update(self.render_footer())

                time.sleep(3)  # 每3秒更新一次

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ashare_t1_tui.py <config_path>")
        sys.exit(1)

    tui = AShareT1TUI(sys.argv[1])
    tui.run()
```

## 3. 测试策略

### 3.1 单元测试

```csharp
// Tests/Common/Securities/AShareStockTests.cs
[TestFixture]
public class AShareStockTests
{
    [Test]
    public void T1Settlement_BuyToday_CannotSellToday()
    {
        // Arrange
        var algorithm = new QCAlgorithm();
        algorithm.SetStartDate(2020, 1, 1);
        algorithm.SetCash(100000);

        var symbol = algorithm.AddEquity("000001", Resolution.Daily, Market.SZSE).Symbol;

        // Act: Buy on day 1
        algorithm.SetHoldings(symbol, 0.5);

        // Assert: Cannot sell on same day
        var holdings = algorithm.Portfolio[symbol];
        Assert.AreEqual(0, holdings.AvailableQuantity);

        // Act: Next day
        algorithm.SetDateTime(algorithm.Time.AddDays(1));

        // Assert: Can sell next day
        Assert.Greater(holdings.AvailableQuantity, 0);
    }

    [Test]
    public void FeeModel_CalculatesCorrectFees()
    {
        // Arrange
        var feeModel = new AShareStockFeeModel();
        var order = new MarketOrder(symbol, 1000, DateTime.Now);
        order.Price = 10.0m;

        // Act
        var fee = feeModel.GetOrderFee(new OrderFeeParameters(security, order));

        // Assert
        // Commission: 10000 * 0.0003 = 3, but minimum is 5
        // Transfer fee (SSE): 10000 * 0.00002 = 0.2
        // Total: 5.2
        Assert.AreEqual(5.2m, fee.Value.Amount);
    }
}
```

## 4. 配置文件示例

```json
// Launcher/config/config-ashare-t1-backtest.json
{
  "environment": "backtesting",
  "algorithm-type-name": "AShareT1MeanReversionAlgorithm",
  "algorithm-language": "CSharp",
  "algorithm-location": "../../../Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll",

  "data-folder": "../../../Data",
  "history-provider": "TushareHistoryProvider",

  "parameters": {
    "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
    "universe": ["000001.SZ", "600000.SH", "000002.SZ"],
    "lookback-period": 20,
    "entry-threshold": -2.0,
    "exit-threshold": 0.0,
    "max-positions": 10,
    "position-size": 0.1
  },

  "backtest": {
    "start-date": "2020-01-01",
    "end-date": "2025-12-31",
    "initial-capital": 1000000,
    "benchmark": "000300.SH"
  }
}
```

## 5. 常见问题

### Q1: T+1约束如何实现？
A: 使用`DelayedSettlementModel(1, TimeSpan.FromHours(9))`，LEAN会自动跟踪unsettled cash和可卖数量。

### Q2: 如何处理涨跌停？
A: 在`FillModel`中检查订单价格是否超出涨跌停限制，超出则拒绝或调整。

### Q3: 实时数据延迟怎么办？
A: Tushare免费版有15分钟延迟，建议使用付费版或接受延迟。

### Q4: 如何优化回测速度？
A: 使用数据预加载、多进程回测、增量计算等技术。

---

**文档版本**: v1.0
**创建日期**: 2025-03-09
