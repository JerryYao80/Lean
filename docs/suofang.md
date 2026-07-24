# A股数据缩放问题根因分析

## 问题现象

A股 equity 数据在 LEAN 回测中被莫名缩放 10000 倍，导致：
- equity close: 2.452 → 0.0002452
- option settle: 0.4032 (正常，未缩放)
- ImpliedVolatility 求解失败（underlying price 异常）

## 根因定位（Phase 1 完成）

### 代码位置

**`Common/Data/Market/TradeBar.cs`**：

```csharp
// 第36行：硬编码缩放因子
private const decimal _scaleFactor = 1 / 10000m;

// 第383行：ParseEquity 无条件调用
private static void ParseEquity(TradeBar tradeBar, SubscriptionDataConfig config, string line, DateTime date)
{
    LineParseScale(config, line, date, useScaleFactor: true, tradeBar, hasVolume: true);  // ← 硬编码 true
}

// 第700-703行：应用缩放
if (useScaleFactor)
{
    tradeBar.Open *= _scaleFactor;    // 2.452 → 0.0002452
    tradeBar.High *= _scaleFactor;
    tradeBar.Low *= _scaleFactor;
    tradeBar.Close *= _scaleFactor;
}
```

而 **Option 数据** 使用条件判断（第548行）：
```csharp
LineParseScale(config, line, date, useScaleFactor: LeanData.OptionUseScaleFactor(config.Symbol), ...)
```

## A股 vs 美股数据格式差异

| 维度 | 美股数据 | A股数据 | LEAN 处理 |
|------|---------|---------|----------|
| **存储格式** | 价格×10000 整数 (24520000) | 真实价格 (2.452) | - |
| **LEAN读取** | ÷10000 还原 → 2.452 ✓ | ÷10000 错误 → 0.0002452 ❌ | 硬编码 |
| **设计假设** | 美股数据源（QuantQuote/IQFeed）存储为"分"以省空间 | tushare/本地 CSV 存真实价格 | 无市场区分 |

**LEAN 的历史设计**：为美股数据源优化，假设所有 equity CSV 存储时 ×10000，读取时统一 ÷10000。`ParseEquity` 硬编码这个逻辑，**没有区分市场**。

## 为什么是系统性问题

**所有走 `TradeBar.ParseEquity` 的 A股 equity 数据都会被 ÷10000**。

这解释了为什么"其他地方也莫名出现缩放问题"——这是 A股接入 LEAN 的系统性陷阱：

- 任何直接读 LEAN 原生 equity CSV 的 A股代码都会中招
- Barra 因子算法、Alpha 模型等如果用 equity 价格，都会被错误缩放
- 之前的 510050 equity 数据在 CSV 里是 2.452，LEAN 读进来是 0.0002452

## 为什么 Option 未缩放

Option 数据走 `ParseOption`（第548行），使用 `LeanData.OptionUseScaleFactor(symbol)` 判断：
- 该方法检查 symbol 的 `SecurityType` 和其他属性
- 我们生成的 zip 内部价格已经是 ×10000 整数，÷10000 后还原正确

但 **equity 的 `ParseEquity` 硬编码 `useScaleFactor: true`，无条件缩放**。

## 正确解法路径

### 路径 A：数据层对齐（推荐，符合原则）

**原则**：Never Modify LEAN Native — 永远不修改 LEAN 核心，修复输入数据而非 LEAN 代码。

**做法**：A股 equity CSV 存储时 ×10000（与美股格式一致），让 LEAN 的 ÷10000 还原正确。

```bash
# 数据转换时
原始价格: 2.452
存储价格: 24520  (×10000)
LEAN读取: 24520 ÷ 10000 = 2.452 ✓
```

**影响**：
- 需改 TushareDataConverter 或数据生成脚本
- 需重新生成现有 A股 equity 数据
- 但一次修复，永久解决

### 路径 B：代码层修复（违反原则）

修改 `ParseEquity` 增加市场判断，让 A股 market 不走 `useScaleFactor:true`。

**问题**：
- 违反"不修改 LEAN 核心"原则
- 每次升级 LEAN 需重新 patch
- 不是正确解法

### 路径 C：PriceMagnifier 配置（已验证无效）

修改 `symbol-properties-database.csv` 的 `price_magnifier` 字段，期望 LEAN 用它做缩放补偿。

**验证结果**：
- price_magnifier 从 10000 改为 1，equity 仍被 ÷10000
- `ParseEquity` 不读取 price_magnifier，直接用硬编码 `_scaleFactor`
- 此路径无效

## 验证方法

### 确认问题存在

```bash
cd /home/project/hope/Lean
grep -n "_scaleFactor\|useScaleFactor: true" Common/Data/Market/TradeBar.cs
# 第36行: private const decimal _scaleFactor = 1 / 10000m;
# 第383行: LineParseScale(..., useScaleFactor: true, ...)
```

### 添加诊断日志

```csharp
// 在算法 OnData 中
if (Time.Day == 13)
{
    Log($"DBG underlying={S} (expect 2.45, got 0.0002?)");
}
```

### 检查现有 A股数据

```bash
# equity CSV 存储格式
head Data/equity/sse/daily/510050.csv
# 20240613 00:00,2.466,2.466,2.445,2.452,7129564  ← 真实价格

# option zip 内部格式
unzip -p Data/option/sse/daily/510050_2024_quote_american.zip | head -1
# 20240603 00:00,1601,1601,...  ← ×10000 整数
```

## 相关文件

- `Common/Data/Market/TradeBar.cs` — 硬编码 ÷10000 位置
- `Common/Data/Market/Tick.cs:849` — GetScaleFactor 也硬编码 10000
- `Common/Securities/SymbolProperties.cs` — price_magnifier 定义（未被 ParseEquity 使用）
- `Data/symbol-properties/symbol-properties-database.csv` — 市场配置（无效）
- `Common/Data/Custom/AShareStockData.cs` — A股自定义数据类型（继承 TradeBar，同样被缩放）

## 历史背景

LEAN 源自美股量化平台 QuantConnect，数据源假设为：
- QuantQuote/IQFeed 等美股数据商
- 价格存储为"分"（cents × 100）或更高精度（×10000）
- 读取时统一 ÷10000 还原为美元价格

A股数据源（tushare/Wind）直接存真实价格（元），无此约定。LEAN 的硬编码设计导致 A股数据被二次缩放。

## 修复状态

- [ ] **数据层 ×10000 对齐**（待实现）
- [x] 根因定位完成
- [x] 验证路径 C 无效
- [x] 文档记录

## 下一步

按"数据层对齐"方向修复：

1. 修改 A股 equity CSV 生成脚本，存储时 ×10000
2. 或创建 A股专用 Reader，绕过 `ParseEquity`
3. 验证修复后 underlying price 正常（~2.45）
4. 重新跑 IV/VIX 回测

**切记**：不修改 `TradeBar.cs`，保持 LEAN 核心不变。