# 筹码峰策略 — cyq_perf 数据基础重构说明

> **日期**: 2026-06-26
> **背景**: 原计划使用 `cyq_chips` 作为多桶筹码分布，但实测发现 cyq_chips 每个 trade_date 只有 1 行单点位数据，无法支撑 Shannon 熵集中度等多桶因子数学。重构改用 `cyq_perf`。

---

## 一、问题根因

### cyq_chips 实测结构（单点位，不可用）

```
cyq_chips/600519.SH/data.parquet
  Shape: (166, 4)  columns: ts_code, trade_date, price, percent
  Rows per trade_date: min=1, max=1, mean=1.00  ← 每交易日仅1行
  20231113: price=2310.0, percent=0.26  ← 单价位，非分布
```

5 只随机抽样股票全部如此。`price` 是当日成交均价，`percent` 是该价位筹码集中度标量，**不是多桶分布直方图**。原计划基于 Shannon 熵的 `concentration` 和累加式 `profit_ratio` 在此数据上完全退化。

### 为什么没早发现

1. 计划/spec 盲信"cyq_chips 是多桶分布"假设，**从未打开 parquet 验证**
2. Task 1-3 单元测试用**手工合成多桶 DataFrame**，与真实数据结构不符，测试给虚假信心
3. IC 验证脚本（Task 4）是骨架版，只检查"可计算性"，不检查分布有效性，掩盖退化

---

## 二、cyq_perf —— 正确的数据基础

`cyq_perf`（筹码及胜率）每个 trade_date 1 行，但包含 **5 档成本分位**，构成离散化的筹码分布：

| 字段 | 含义 | 用途 |
|------|------|------|
| `his_low` / `his_high` | 筹码历史最低/最高成本 | 分布范围边界 |
| `cost_5pct` | 5% 筹码成本分位 | 低成本区下沿 |
| `cost_15pct` | 15% 筹码成本分位 | |
| `cost_50pct` | 50% 筹码成本分位（中位成本） | 成本中枢 |
| `cost_85pct` | 85% 筹码成本分位 | |
| `cost_95pct` | 95% 筹码成本分位 | 高成本区上沿 |
| `weight_avg` | **加权平均成本** | 直接可用（无需累加） |
| `winner_rate` | **获利盘比例 %** | 直接可用（无需累加） |

覆盖率：5536 只股票，2018-01 至 2026-06，数据完整。

---

## 三、重构后的因子数学（标量版）

因子输入从"多桶 DataFrame"改为**单行 `pd.Series`**（cyq_perf 一行 + adj 校正字段）。

### 1. concentration（筹码集中度）

原方案用 Shannon 熵，需要多桶。新方案用**成本带宽**：

```
spread = (cost_95pct - cost_5pct) / weight_avg   # 90%成本相对宽度
concentration = exp(-spread)                      # [0,1], 宽度越小越集中
```

- 高度控盘（单峰密集）：spread 小 → concentration → 1
- 发散（多峰/宽分布）：spread 大 → concentration → 0

带宽法对 adj_factor 不敏感（分子分母同尺度），稳健。

### 2. profit_ratio（获利盘比例）

```
profit_ratio = winner_rate / 100.0   # 直接取，无需累加
```

### 3. average_cost（平均成本）

```
average_cost = weight_avg   # 直接取
```

### 4. cost_deviation（平均成本偏离度）

```
cost_deviation = (current_price - weight_avg_adj) / weight_avg_adj
```

`weight_avg_adj = weight_avg * adj_factor`，与 LEAN 复权价同尺度。

### 5. classify_peak（形态分类，逻辑不变）

```
if concentration < single_concentration(0.6): return DIVERGENT
if profit_ratio < low_profit_max(0.2):        return LOW_SINGLE_PEAK   # 建仓区
if profit_ratio > high_profit_min(0.8):       return HIGH_SINGLE_PEAK  # 派发区
return DIVERGENT
```

### 6. composite_score（综合得分，逻辑不变）

```
LOW_SINGLE_PEAK → conc * (1 - profit) * (1 + max(0, dev))   # 正分
其余 → 0
```

---

## 四、复权校正

cyq_perf 成本字段为原始（未复权）价位。LEAN 行情为复权价。统一用 `adj_factor` 校正：

```
cost_x_adj = cost_x * adj_factor
weight_avg_adj = weight_avg * adj_factor
```

`winner_rate` 与 `concentration`（带宽比）天然 adj 不变，无需校正。

> **已知注意点**: cost_deviation 的准确性依赖 current_price 与 weight_avg_adj 在同一复权口径。当前用 `* adj_factor`（后复权）统一，与原计划 Task 1 约定一致。若 LEAN daily 数据为前复权，需进一步除以最新 adj_factor；Phase 1 先用后复权口径，回测后按偏差再校准。

---

## 五、文件改动清单

**重写（已提交但基于错误数据假设）:**
- `ToolBox/ChipDataLoader.py` — 改读 cyq_perf，load_single 返回 `pd.Series`
- `Algorithm.Python/ChipPeakFactors.py` — 标量版因子（带宽集中度）
- `Tests/Python/Scripts/ChipDataLoaderTests.py` — 改测 cyq_perf
- `Tests/Python/Scripts/ChipPeakFactorsTests.py` — 改测标量因子
- `ToolBox/ChipPeakICValidator.py` — 改用 cyq_perf

**新建（Task 5-9，按新 API）:**
- `Algorithm.Python/AShareUniverseSelectionModel.py`
- `Algorithm.Python/ChipPeakAlphaModel.py` — composite_score(chip_row, price, params)
- `Algorithm.Python/ChipPeakRiskManagementModel.py`
- `Algorithm.Python/ChipPeakStrategyAlgorithm.py`
- `Launcher/config/config-chip-peak.json`
