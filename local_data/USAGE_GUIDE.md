# A-Share ETF T+0 实时交易系统使用指南

## 系统概览

本系统基于 LEAN 架构，实现 A 股 ETF T+0 日内交易策略，包含：
- ✅ 实时日线数据获取（Tushare API）
- ✅ 特征计算和持久化
- ✅ 交易信号生成预览
- ✅ 自动交易执行（LEAN Engine）
- ✅ 持仓管理和风险控制
- ✅ 交易报告和 P&L 跟踪

## 快速开始

### 1. 运行实时交易系统

```bash
cd /home/project/hope/Lean
python Scripts/ashare_etf_t0_feature_live_paper.py \
  --config Launcher/config/config-ashare-etf-t0-feature-live-paper.json
```

### 2. 仅运行数据 Bridge（不执行交易）

```bash
cd /home/project/hope/Lean
python Scripts/ashare_etf_t0_feature_live_bridge.py \
  --config Launcher/config/config-ashare-etf-t0-feature-live-paper.json
```

### 3. 单次测试（不循环）

```bash
python Scripts/ashare_etf_t0_feature_live_bridge.py \
  --config Launcher/config/config-ashare-etf-t0-feature-live-paper.json \
  --once
```

## 输出说明

### 1. 数据下载阶段

```
================================================================================
🔄 Refreshing live features at 2026-03-11 14:30:00
📅 Session: 20260311 | Universe: 119 symbols
================================================================================

📊 Fetching daily data for 119 symbols...
  [1/119] 159323.SZ... 📈 ¥1.29 (+2.88%)
  [2/119] 159239.SZ... 📈 ¥0.99 (+2.92%)
  [3/119] 159210.SZ... 📈 ¥1.02 (+2.61%)
  ...

✅ Successfully fetched 118 records
```

**说明：**
- 显示当前时间和交易日期
- 逐个下载 ETF 实时日线数据
- 显示价格和涨跌幅
- 📈 上涨 | 📉 下跌 | ➡️ 平盘

### 2. 特征处理阶段

```
📝 Processing 118 quotes...
────────────────────────────────────────────────────────────────────────────────
  [1/118] 159323.SZ    ✅ ¥   1.29 📈 +2.88% | M5: +5.23% | Vol:  1.45
  [2/118] 159239.SZ    ✅ ¥   0.99 📈 +2.92% | M5: +4.87% | Vol:  1.32
  [3/118] 159210.SZ    ✅ ¥   1.02 📈 +2.61% | M5: +3.45% | Vol:  1.18
  ...
────────────────────────────────────────────────────────────────────────────────
✅ Written: 118 | ⏭️  Skipped: 1
```

**说明：**
- 计算技术特征（动量、波动率等）
- M5: 5日动量
- Vol: 10日波动率
- 保存到 CSV 文件

### 3. 信号生成预览

```
====================================================================================================
📊 SIGNAL GENERATION PREVIEW
====================================================================================================

🔍 Analyzing 118 symbols...

📈 SYMBOL RANKING (Total: 118)
────────────────────────────────────────────────────────────────────────────────────────────────────
  Rank | Symbol       |    Score |      Price |   Change | Signal
────────────────────────────────────────────────────────────────────────────────────────────────────
     1 | 159323.SZ    |   0.1234 |    ¥1.29   |  +2.88% | 🟢 BUY
     2 | 159239.SZ    |   0.1156 |    ¥0.99   |  +2.92% | 🟢 BUY
     3 | 159210.SZ    |   0.0987 |    ¥1.02   |  +2.61% | ⚪ HOLD
     4 | 159121.SZ    |   0.0876 |    ¥0.94   |  +2.74% | ⚪ HOLD
  ...

  ✅ Score spread: 0.0078 >= 0.0070 (threshold)
  🎯 Generating BUY signals for top 2 symbols

🟢 BUY SIGNALS (2):
────────────────────────────────────────────────────────────────────────────────────────────────────
  159323.SZ    | Weight: 50.00% | Score:  0.1234 | Price:    ¥1.29 | Change: +2.88%
  159239.SZ    | Weight: 50.00% | Score:  0.1156 | Price:    ¥0.99 | Change: +2.92%
====================================================================================================
```

**说明：**
- 显示所有符号的评分排名
- 🟢 BUY: 买入信号
- 🔴 SELL: 卖出信号
- ⚪ HOLD: 持有/观望
- Score: 综合评分（越高越好）
- Weight: 目标仓位权重

### 4. 无信号情况

```
====================================================================================================
📊 SIGNAL GENERATION PREVIEW
====================================================================================================

🔍 Analyzing 118 symbols...

  ⚠️  Score spread: 0.0045 < 0.0070 (threshold)
  ⏸️  No trading signals - spread too narrow

⚪ NO BUY SIGNALS
  Reason: Score spread 0.0045 < threshold 0.0070
====================================================================================================
```

**说明：**
- 即使没有信号也会显示原因
- 不会让你空等黑盒
- 可以看到评分差距是否足够

## 查看交易结果

### 实时监控

```bash
# 实时查看交易记录
tail -f Results/ashare-etf-t0-feature-live-trades.csv

# 实时查看持仓
tail -f Results/ashare-etf-t0-feature-live-allocation.csv

# 实时查看每日汇总
tail -f Results/ashare-etf-t0-feature-live-daily-summary.csv
```

### 查看历史记录

```bash
# 交易记录
cat Results/ashare-etf-t0-feature-live-trades.csv

# 每日汇总（包含 P&L）
cat Results/ashare-etf-t0-feature-live-daily-summary.csv

# 当前持仓
cat Results/ashare-etf-t0-feature-live-allocation.csv

# 行动计划
cat Results/ashare-etf-t0-feature-live-action-plan.csv
```

## 配置参数

### 核心参数（在 config.json 中）

```json
{
  "top-n": "2",                          // 选择排名前 N 的 ETF
  "min-score-spread": "0.7",             // 最小评分差距（阈值）
  "max-average-gap-abs": "0.016",        // 最大跳空幅度过滤
  "target-portfolio-exposure": "0.95",   // 目标仓位暴露度（95%）
  "live-feature-poll-interval-seconds": "30"  // 数据刷新间隔（秒）
}
```

### 信号权重（在代码中）

```python
signal_weights = {
    'signal_momentum_20': 0.25,      # 20日动量（25%）
    'signal_momentum_5': 0.20,       # 5日动量（20%）
    'signal_liquidity_5': 0.15,      # 5日流动性（15%）
    'signal_close_location': 0.10,   # 收盘位置（10%）
    'signal_volatility_10': -0.10,   # 10日波动率（-10%，越低越好）
    'signal_nav_premium_z20': -0.10, # 溢价率（-10%，越低越好）
    'signal_excess_intraday': 0.10,  # 超额日内收益（10%）
    'signal_index_momentum_5': 0.10, # 指数5日动量（10%）
}
```

## 数据存储

### 特征数据

```
Data/alternative/ashare-etf-t0-live-features/
├── 159323.SZ/
│   └── features.csv
├── 159239.SZ/
│   └── features.csv
└── ...
```

### 原始行情归档

```
Data/archive/daily_quotes/
├── date=20260311/
│   └── quotes_20260311.parquet
├── date=20260312/
│   └── quotes_20260312.parquet
└── ...
```

### 交易报告

```
Results/
├── ashare-etf-t0-feature-live-trades.csv          # 交易记录
├── ashare-etf-t0-feature-live-daily-summary.csv   # 每日汇总
├── ashare-etf-t0-feature-live-allocation.csv      # 持仓分配
└── ashare-etf-t0-feature-live-action-plan.csv     # 行动计划
```

## 策略逻辑

### 1. 数据获取
- 每 30 秒（可配置）获取一次实时日线
- 使用 Tushare `rt_etf_k` API

### 2. 特征计算
- 动量指标（5日、20日）
- 波动率（10日）
- 流动性（5日平均成交额）
- 溢价率（相对净值）
- 超额收益（相对指数）

### 3. 信号生成
- 计算综合评分（加权平均）
- 排序选择 Top N
- 检查评分差距是否足够大
- 生成 BUY/SELL 信号

### 4. 交易执行
- LEAN Engine 自动执行
- T+0 日内交易（当日买入可当日卖出）
- 目标仓位：95%
- 初始资金：1,000,000 CNY

### 5. 风险控制
- 最大跳空过滤
- 评分差距阈值
- 波动率控制
- 仓位限制

## 常见问题

### Q: 为什么没有生成交易信号？

A: 可能的原因：
1. 评分差距不够大（< min-score-spread）
2. 符号数量不足（< top-n）
3. 跳空幅度过大（> max-average-gap-abs）
4. 市场休市（非交易时段）

### Q: 如何调整策略参数？

A: 修改 `Launcher/config/config-ashare-etf-t0-feature-live-paper.json` 中的参数，然后重启系统。

### Q: 如何查看实际持仓和 P&L？

A: 查看 `Results/ashare-etf-t0-feature-live-allocation.csv` 和 `Results/ashare-etf-t0-feature-live-daily-summary.csv`

### Q: 信号预览和实际交易有什么区别？

A:
- **信号预览**：Bridge 中的只读预览，不执行交易
- **实际交易**：LEAN Engine 执行，会生成订单和持仓

### Q: 如何停止系统？

A: 按 `Ctrl+C` 停止运行

## 注意事项

1. **API 权限**：确保 Tushare token 有 `rt_etf_k` 接口权限
2. **交易时段**：默认仅在交易时段（9:30-11:30, 13:00-15:00）运行
3. **数据延迟**：实时数据可能有几秒到几分钟的延迟
4. **Paper Trading**：当前为模拟交易，不会实际下单
5. **资金管理**：初始资金 100 万 CNY，目标仓位 95%

## 架构说明

```
Tushare API
    ↓
Bridge (数据层)
    ├─ 获取实时日线
    ├─ 计算特征
    ├─ 归档数据
    └─ 信号预览（只读）
    ↓
LEAN Engine (引擎层)
    ├─ 读取特征数据
    ├─ 生成交易信号
    ├─ 执行订单
    └─ 管理持仓
    ↓
Results (输出层)
    ├─ 交易记录
    ├─ 持仓报告
    └─ P&L 汇总
```

**关键原则：**
- Bridge 只负责数据，不执行交易
- Algorithm 负责交易逻辑
- Engine 负责订单执行
- 职责分明，易于维护

## 更多信息

- LEAN 架构指南：`local_data/LEAN_ARCHITECTURE_GUIDE.md`
- 迁移文档：`local_data/rt_daily_migration.md`
- LEAN 官方文档：https://www.lean.io/docs/
