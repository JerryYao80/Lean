# LEAN 架构指南：正确实现交易信号和持仓管理

## 架构原则

LEAN 采用分层架构，每层有明确的职责：

```
┌─────────────────────────────────────────────────────────────┐
│  Algorithm Layer (C#/Python)                                │
│  - 交易逻辑和信号生成                                          │
│  - 持仓管理和风险控制                                          │
│  - 继承 QCAlgorithm                                          │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Engine Layer (C#)                                          │
│  - 事件驱动执行循环                                            │
│  - 订单处理和撮合                                              │
│  - 结果统计和报告                                              │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Data Layer (C#/Python)                                     │
│  - 数据获取和规范化                                            │
│  - 历史数据和实时数据                                          │
│  - 自定义数据源                                                │
└─────────────────────────────────────────────────────────────┘
```

## 当前实现状态

### ✅ 已实现（符合架构）

1. **Data Layer - Bridge 脚本**
   - 位置：`Scripts/ashare_etf_t0_feature_live_bridge.py`
   - 职责：
     - 从 Tushare API 获取实时日线数据
     - 计算特征（momentum, volatility, liquidity等）
     - 持久化特征数据到 CSV
     - 归档原始行情数据到 Parquet
   - ✅ 符合 LEAN 数据层职责

2. **Algorithm Layer - C# 算法**
   - 位置：`Algorithm.CSharp/AShareEtfT0FeatureIntradayAlgorithm.cs`
   - 职责：
     - 读取特征数据（通过 `AShareEtfT0FeatureData`）
     - 生成交易信号（通过 `AShareEtfT0FeatureSignalModel`）
     - 执行交易决策
     - 管理持仓和风险
   - ✅ 符合 LEAN 算法层职责

3. **Engine Layer - LEAN 核心**
   - 位置：`Engine/` 目录
   - 职责：
     - 驱动算法执行
     - 处理订单和撮合
     - 生成交易报告
   - ✅ LEAN 内置，无需修改

## 如何查看交易信号和持仓

### 方式 1：运行 Live-Paper 模式（推荐）

```bash
cd /home/project/hope/Lean
python Scripts/ashare_etf_t0_feature_live_paper.py \
  --config Launcher/config/config-ashare-etf-t0-feature-live-paper.json
```

**输出位置：**
- 交易记录：`Results/ashare-etf-t0-feature-live-trades.csv`
- 每日汇总：`Results/ashare-etf-t0-feature-live-daily-summary.csv`
- 持仓分配：`Results/ashare-etf-t0-feature-live-allocation.csv`
- 行动计划：`Results/ashare-etf-t0-feature-live-action-plan.csv`

### 方式 2：查看算法日志

LEAN 会在控制台输出：
- 订单提交和成交信息
- 持仓变化
- 每日 P&L
- 风险指标

### 方式 3：查看结果文件

算法会生成以下文件（配置在 `config.json` 的 `parameters` 中）：

```json
{
  "trade-report-file": "ashare-etf-t0-feature-live-trades.csv",
  "daily-summary-file": "ashare-etf-t0-feature-live-daily-summary.csv",
  "allocation-report-file": "ashare-etf-t0-feature-live-allocation.csv",
  "action-plan-file": "ashare-etf-t0-feature-live-action-plan.csv"
}
```

## 数据流

```
1. Tushare API (rt_etf_k / rt_k)
   ↓
2. Bridge 脚本获取实时日线
   ↓
3. 计算特征 (momentum, volatility, etc.)
   ↓
4. 保存到 CSV (Data/alternative/ashare-etf-t0-live-features/)
   ↓
5. 归档原始数据 (Data/archive/daily_quotes/)
   ↓
6. LEAN Engine 读取特征数据
   ↓
7. Algorithm 生成交易信号
   ↓
8. Engine 执行订单
   ↓
9. 输出交易报告 (Results/)
```

## 如何增强功能（正确方式）

### ❌ 错误做法

- 在 Scripts/ 中创建独立的信号生成器
- 在 Bridge 中混入交易逻辑
- 绕过 LEAN Engine 直接执行交易

### ✅ 正确做法

#### 1. 增强数据层（Bridge）

如果需要新的特征：

```python
# 在 ashare_etf_t0_feature_live_bridge.py 中
def build_live_feature_row(...):
    # 添加新特征计算
    row['new_feature'] = calculate_new_feature(...)
    return row
```

#### 2. 增强算法层（C# Algorithm）

如果需要新的交易逻辑：

```csharp
// 在 AShareEtfT0FeatureIntradayAlgorithm.cs 中
public override void OnData(Slice data)
{
    // 添加新的信号逻辑
    var signals = GenerateSignals(data);

    // 执行交易
    foreach (var signal in signals)
    {
        SetHoldings(signal.Symbol, signal.Weight);
    }
}
```

#### 3. 增强信号模型（C# Signal Model）

如果需要新的信号计算：

```csharp
// 在 AShareEtfT0FeatureSignalModel.cs 中
public decimal CalculateCompositeScore(AShareEtfT0FeatureData features)
{
    // 添加新的评分逻辑
    var score = features.Momentum20 * 0.25m
              + features.Momentum5 * 0.20m
              + NewFeature * 0.15m;  // 新特征
    return score;
}
```

## 实时监控交易信号

### 方案 1：增强 Bridge 输出（推荐）

在 Bridge 中添加信号预览（不执行交易）：

```python
def preview_signals(feature_data: pd.DataFrame, config: dict):
    """预览交易信号（不执行交易）"""
    # 使用与算法相同的逻辑计算信号
    # 仅用于监控，不执行实际交易
    pass
```

### 方案 2：实时读取算法输出

监控 `Results/` 目录中的 CSV 文件：

```bash
# 实时查看交易记录
tail -f Results/ashare-etf-t0-feature-live-trades.csv

# 实时查看持仓
tail -f Results/ashare-etf-t0-feature-live-allocation.csv
```

### 方案 3：添加 WebSocket 推送（高级）

在 Algorithm 中添加实时推送：

```csharp
// 在算法中
private void PublishSignal(Signal signal)
{
    // 推送到 WebSocket / Redis / 消息队列
    // 供外部监控系统使用
}
```

## 当前配置

### Bridge 配置

```json
{
  "feature-data-path": "../../../Data/alternative/ashare-etf-t0-live-features",
  "daily-quote-archive-path": "../../../Data/archive/daily_quotes",
  "live-feature-poll-interval-seconds": "30"
}
```

### Algorithm 配置

```json
{
  "algorithm-type-name": "AShareEtfT0FeatureIntradayAlgorithm",
  "top-n": "2",
  "min-score-spread": "0.7",
  "target-portfolio-exposure": "0.95",
  "execution-mode": "synthetic"
}
```

## 总结

**关键原则：**
1. **数据层（Bridge）**：只负责数据获取、特征计算、持久化
2. **算法层（Algorithm）**：负责信号生成、交易决策、持仓管理
3. **引擎层（Engine）**：负责订单执行、撮合、报告

**不要：**
- 在 Bridge 中生成交易信号
- 在 Scripts 中创建独立的交易系统
- 绕过 LEAN 架构

**要：**
- 遵循 LEAN 分层架构
- 在正确的层实现功能
- 利用 LEAN 的事件驱动机制
- 使用 LEAN 的报告和监控功能

这样才能保持代码清晰、易维护、符合 LEAN 设计哲学。
