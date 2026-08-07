# P2-A: IC/IR 报告回灌 C# 运行时 — 设计文档

> 日期：2026-08-06
> 范围：P2 五层架构缺口 的第一个子项目（开门砖）
> 父文档：`docs/backsee.md` 任务 #10（P2）、未打通链路 #3（Python 研究层 → C# 运行时）

## 一、目标

打通 **Python 研究层 → C# 运行时** 的 IC 回灌链路。让 alpha 模型从硬编码 49 因子 + 权重，改为读离线生成的 expanding-window IC 报告 JSON，实现**无前视（no lookahead bias）、自适应**的因子选择与加权。

这是 P2 的"开门砖"：做完即验证"Python 研究层 → C# 运行时"链路通了，是后续 P2-B（真 MVO + 协方差）、P2-C（Barra 因子风险分解）的前提。

## 二、约束（不可违反）

1. **零侵入成熟特性**（[[never-modify-existing-features]]）：
   - 旧 `ICWeightedAlphaModel.cs` 一行不改，继续硬编码 49 因子。
   - 旧 `AShareCSI300EnhancedStrategy.cs` 一行不改。
   - 旧 `config_enhanced.json` 一行不改。
   - 新功能全部在新文件里。
2. **LEAN-native**（[[lean-native-only]] / [[never-modify-lean-native]]）：C# 纯运行时，不碰 LEAN 核心；指标由 LEAN 产。
3. **不回填因子到 2016**：归 P1-5 独立后续任务。P2-A 用因子 parquet 物理下限 2020-01-02 作为 expanding 起点。
4. **C# 端不做 IC 计算**：IC 计算在 Python 研究层，C# 只读 JSON。

## 三、架构

三个独立组件，互不侵入：

```
[离线研究层 Python]                    [运行时 C#]                    [策略层]
export_ic_reports.py (新建)           ICWeightedAlphaModelV2.cs      AShareCSI300EnhancedV2Strategy.cs (新建)
  复用 ic_ir_engine.py        →JSON→   (新建，读 JSON)        ←new→   config_enhanced_v2.json (新建)
  + factor_panel_loader.py              旧 ICWeightedAlphaModel         旧 config_enhanced.json 不动
  按 expanding window 月端              一行不动
  生成 ic_report_*.json
```

## 四、组件 1：离线 IC 报告生成脚本（Python）

### 4.1 文件
新建 `Scripts/factor_zoo/export_ic_reports.py`，复用已有 `ic_ir_engine.py` + `factor_panel_loader.py`（不改动这两个已有文件）。

### 4.2 逻辑
- 按月端遍历（2020-01-31, 2020-02-29, ..., 至最新可用因子日期）。
- 每个月端用 **expanding window**（该月端之前所有可用因子数据）算 IC 报告。
- 调 `ICIREngine.compute_ic_report(start_date=ic_window_start, end_date=month_end, horizon=21)`。
  - `ic_window_start` = 2020-01-02（固定 expanding 起点）。
  - `end_date` = 当前月端（exclusive：报告只用 `< month_end` 的数据，确保无前视）。
- 调 `ICIREngine.filter_factors(report, min_ic=0.02, min_ir=0.3)` 筛因子（Python 端筛，C# 不筛）。
- 导出 JSON 到 `result/ic-reports/ic_report_YYYY-MM-DD.json`。

### 4.3 参数（已定）
| 参数 | 值 | 理由 |
|---|---|---|
| expanding 起点 | 2020-01-02 | 因子 parquet 物理下限（用户要求比 2020 早，但数据下限即此；回填到 2016 归 P1-5） |
| IC horizon | 21 天 | 月度 forward return，与调仓周期一致 |
| min_ic | 0.02 | `ic_ir_engine.py` 默认 |
| min_ir | 0.3 | `ic_ir_engine.py` 默认 |
| top_n | 不固定 | 由阈值决定，每月入选数自适应 |
| 月端生成 | 是 | 与月度调仓对齐 |

### 4.4 缓存
复用 `ic_ir_engine.py` 的 `result/ic-cache/` pickle 缓存（`compute_ic_report` 内置），避免重算。expanding window 每个月端调用一次 `compute_ic_report`，每个区间独立缓存。

### 4.5 幂等与重跑
- 脚本检查目标 JSON 是否已存在且 `report_date` 匹配 → 跳过。
- `--force` 参数强制重算（清缓存重跑）。

## 五、JSON Schema

每份文件 `result/ic-reports/ic_report_YYYY-MM-DD.json`：

```json
{
  "report_date": "2024-01-31",
  "ic_window_start": "2020-01-02",
  "ic_window_end": "2024-01-31",
  "horizon": 21,
  "min_ic": 0.02,
  "min_ir": 0.3,
  "factors": [
    {"factor_id": "alpha012", "rank_ic_mean": 0.052, "rank_ic_ir": 0.42, "weight": 0.0286},
    {"factor_id": "alpha077", "rank_ic_mean": 0.048, "rank_ic_ir": 0.39, "weight": 0.0263}
  ]
}
```

- `weight` = `|rank_ic_mean|` 归一化到 sum=1（与 `ic_weighted_alpha.py:136-156` 逻辑一致）。
- `factors` 已按 `rank_ic_ir` 降序排列（`filter_factors` 内置排序）。
- 按月端日期命名文件，C# 取 `≤ 当前调仓日` 的最新一份。

## 六、组件 2：C# 运行时模型

### 6.1 文件
新建 `Algorithm.CSharp/Models/Alpha/ICWeightedAlphaModelV2.cs`，`namespace QuantConnect.Algorithm.CSharp.Models.Alpha`，实现 `IAlphaModel`。

### 6.2 结构
镜像旧 `ICWeightedAlphaModel`（月度调仓、top quantile、FactorStore pull），关键差异：

- 构造函数：
  ```csharp
  public ICWeightedAlphaModelV2(
      string icReportDir,                    // 如 /home/project/hope/Lean/result/ic-reports
      int rebalanceMonths = 1,
      int insightPeriodDays = 21,
      decimal topQuantile = 0.10m,
      FactorStore store = null)              // 可注入测试
  ```
- 调仓时（`Update`）：
  1. 扫描 `icReportDir` 下所有 `ic_report_*.json`。
  2. 取文件名日期 `≤ algorithm.Time` 的最新一份。
  3. Newtonsoft.Json 反序列化，读 `factors[].factor_id` + `weight`。
  4. 用这些因子 + 权重算 composite score（横截面 z-score 加权，逻辑同旧模型 `ComputeAlphaScores`）。
  5. 选 top quantile，生成 `Insight.Price`（weight = 1/n）。

### 6.3 降级与容错
| 情况 | 行为 |
|---|---|
| `icReportDir` 不存在或为空 | log 警告 + 等权 top N fallback（用 FactorStore 中所有可读因子，不 crash） |
| 无 `≤ 当前日` 的报告 | 用目录里最早的报告 + log（回测起点附近边界） |
| JSON 损坏/反序列化失败 | log 警告 + 等权 fallback |
| 某因子 `FactorDataQuality != Valid` | 跳过该因子（旧模型逻辑沿用） |
| 全部因子都无效 | 不产出 Insight + log（旧模型逻辑沿用） |

### 6.4 依赖
- `Newtonsoft.Json`（项目既有惯例，`RlRiskModel.cs` 已用，LEAN 核心依赖，零新增）。
- `FactorStore.Get(fid, symbol, date)`（旧模型已验证可用，复用）。

## 七、组件 3：策略 + Config

### 7.1 策略
新建 `Algorithm.CSharp/AShareCSI300EnhancedV2Strategy.cs`，镜像 `AShareCSI300EnhancedStrategy`，唯一区别：
- `SetAlpha(new ICWeightedAlphaModelV2(icReportDir: ..., rebalanceMonths: 1, insightPeriodDays: rebalanceDays, topQuantile: 0.10m))`
- 其余四层（Universe / Portfolio / Risk / Execution）与旧策略完全相同。
- `icReportDir` 从 config parameter `icReportDir` 读，默认 `/home/project/hope/Lean/result/ic-reports`。

### 7.2 Config
新建 `Launcher/config_enhanced_v2.json`，复制 `config_enhanced.json`，改：
- `"algorithm-type-name": "AShareCSI300EnhancedV2Strategy"`
- `"results-destination-folder": "../../../Results/EnhancedStrategyV2"`
- `parameters` 加 `"icReportDir": "/home/project/hope/Lean/result/ic-reports"`

## 八、测试策略

### 8.1 Python 端（pytest）
- `test_export_ic_reports.py`：
  - 产出合法 JSON、schema 正确（必填字段齐全）。
  - expanding window 无前视：某月端报告的 `ic_window_end` ≤ 该月端，且只用此前数据。
  - `weight` 归一化 sum=1。
  - 幂等：重跑跳过已存在。
  - `--force` 重算覆盖。

### 8.2 C# 端（NUnit）
- `ICWeightedAlphaModelV2Tests`：
  - 读合法 JSON → 因子列表 + 权重正确。
  - 选最新报告（`≤ 当前日`）。
  - 无可用报告 → 等权 fallback。
  - JSON 损坏 → 等权 fallback 不 crash。
  - 权重归一化。
- 测试用构造函数注入 `FactorStore` mock / 临时 JSON 目录，不依赖真实 parquet。

### 8.3 集成
- 用 `config_enhanced_v2.json` 跑一段回测（如 2024-01 ~ 2024-06），确认：
  - 非零订单产出（吸取旧模型 0-order 教训，见 [[ashare_lean_price_scaling]]）。
  - log 显示 V2 读了 JSON、选中了因子。
  - LEAN 原生指标正常产出（Sharpe/drawdown）。

## 九、不做的事（YAGNI）

- 不改旧 `ICWeightedAlphaModel`（零侵入）。
- 不改旧策略 / 旧 config。
- 不回填因子到 2016（归 P1-5）。
- 不在 C# 端做 IC 计算。
- 不碰 LEAN 核心。
- 不做 P2-B/C/D/E（各自独立 spec）。

## 十、交付物清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `Scripts/factor_zoo/export_ic_reports.py` | 新建 | 离线 expanding-window IC 报告生成 |
| `Algorithm.CSharp/Models/Alpha/ICWeightedAlphaModelV2.cs` | 新建 | C# 读 JSON alpha 模型 |
| `Algorithm.CSharp/AShareCSI300EnhancedV2Strategy.cs` | 新建 | 装配 V2 的五层策略 |
| `Launcher/config_enhanced_v2.json` | 新建 | V2 回测 config |
| `Tests/.../ICWeightedAlphaModelV2Tests.cs` | 新建 | NUnit 测试 |
| `Scripts/factor_zoo/tests/test_export_ic_reports.py` | 新建 | pytest 测试 |
| `result/ic-reports/ic_report_*.json` | 生成物 | 月端 IC 报告（离线产出，不入 git） |

## 十一、验证标准（完成定义）

1. `export_ic_reports.py` 产出 ≥ 1 份合法 JSON（至少覆盖 2020-02 ~ 2024-06 月端）。
2. V2 单元测试全绿。
3. `config_enhanced_v2.json` 回测非零订单、LEAN 指标正常。
4. 旧 `ICWeightedAlphaModel` / 旧策略 / 旧 config 零改动（git diff 验证）。
5. 提交并推送（测试验证通过后自动，无需确认）。
