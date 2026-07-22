# Barra CNE5 因子模型 + 蒙特卡洛回测 — 详细设计文档

## 1. 项目目标

基于 Barra CNE5 模型的10个风格因子（21个描述子），针对A股市场构建多因子选股策略，
通过蒙特卡洛模拟进行稳健性回测，严格遵循 LEAN 框架分层架构。

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                    LEAN 分层架构                                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Data Layer (Python)                                          │  │
│  │                                                               │  │
│  │  data-source/tushare/barra_cne5_factor_builder.py            │  │
│  │  ├─ 从 tushare_data/ 读取原始数据                              │  │
│  │  ├─ 计算21个描述子 → 10个风格因子                               │  │
│  │  ├─ 因子标准化 (winsorize + z-score)                          │  │
│  │  ├─ 正交化处理 (ResVol vs Beta)                               │  │
│  │  └─ 输出到 Data/alternative/barra-cne5-factors/              │  │
│  │                                                               │  │
│  │  Scripts/barra_cne5_factor_bridge.py                          │  │
│  │  ├─ 调度因子计算                                               │  │
│  │  ├─ 友好终端输出 (进度、统计、诊断)                              │  │
│  │  └─ 数据质量检查                                               │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                          ↓ CSV                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Algorithm Layer (C#)                                         │  │
│  │                                                               │  │
│  │  AShareBarraCNE5FactorData.cs      — 自定义数据读取            │  │
│  │  AShareBarraCNE5SignalModel.cs      — 多因子评分模型            │  │
│  │  AShareBarraCNE5Algorithm.cs        — 交易算法主体              │  │
│  │  AShareBarraCNE5MonteCarloAlgorithm.cs — 蒙特卡洛回测算法      │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                          ↓                                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Engine Layer (LEAN Core)                                     │  │
│  │  ├─ 事件驱动执行                                               │  │
│  │  ├─ 订单撮合 (T+1 settlement)                                 │  │
│  │  └─ 结果输出 → Results/                                       │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Monte Carlo Layer (Python)                                   │  │
│  │                                                               │  │
│  │  Scripts/barra_cne5_monte_carlo.py                            │  │
│  │  ├─ 读取回测结果                                               │  │
│  │  ├─ 收益率序列 bootstrap / 因子扰动                            │  │
│  │  ├─ N次模拟 → 统计分布                                        │  │
│  │  └─ 输出报告 (置信区间、VaR、最大回撤分布)                      │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 数据源映射 (Tushare → Barra CNE5)

### 3.1 可用数据清单

| Barra 描述子 | 所需数据 | Tushare 数据表 | 字段 | 状态 |
|---|---|---|---|---|
| **BETA** | 日收益率、市场收益率 | `daily` + `index_daily` (000300.SH) | close, pct_chg | ✅ 可用 |
| **RSTR** | 504天日收益率、无风险利率 | `daily` + `shibor` | close, pct_chg, 1y | ✅ 可用 |
| **LNCAP** | 总市值 | `daily_basic` | total_mv | ✅ 可用 |
| **EPIBS** | 分析师预测EPS | 无 | — | ❌ 不可用 |
| **ETOP** | TTM盈利、市值 | `income` + `daily_basic` | n_income_attr_p, total_mv | ✅ 可用 |
| **CETOP** | 现金盈利、市值 | `income` + `cashflow` + `daily_basic` | n_income_attr_p, depr_fa_coga_dpba, amort_intang_assets | ✅ 可用 |
| **DASTD** | 252天日收益率 | `daily` | pct_chg | ✅ 可用 |
| **CMRA** | 12个月月收益率 | `daily` | close | ✅ 可用 |
| **HSIGMA** | Beta回归残差 | `daily` + `index_daily` | 由BETA计算衍生 | ✅ 可用 |
| **SGRO** | 5年销售额 | `income` | revenue | ✅ 可用 |
| **EGRO** | 5年盈利 | `income` | n_income_attr_p | ✅ 可用 |
| **EGIBS** | 分析师长期增长预测 | 无 | — | ❌ 不可用 |
| **EGIBS_s** | 分析师FY1/FY2预测 | 无 | — | ❌ 不可用 |
| **BTOP** | 账面价值、市值 | `balancesheet` + `daily_basic` | total_hldr_eqy_exc_min_int, total_mv | ✅ 可用 |
| **MLEV** | 市值、债务 | `daily_basic` + `balancesheet` | total_mv, lt_borr, st_borr | ✅ 可用 |
| **DTOA** | 总债务、总资产 | `balancesheet` | total_liab, total_assets | ✅ 可用 |
| **BLEV** | 账面权益、债务 | `balancesheet` | total_hldr_eqy_exc_min_int, lt_borr, st_borr | ✅ 可用 |
| **STOM** | 月成交量、流通股 | `daily` + `daily_basic` | vol, float_share | ✅ 可用 |
| **STOQ** | 季成交量、流通股 | `daily` + `daily_basic` | vol, float_share | ✅ 可用 |
| **STOA** | 年成交量、流通股 | `daily` + `daily_basic` | vol, float_share | ✅ 可用 |
| **NLSIZE** | LNCAP立方正交化 | 由LNCAP衍生 | — | ✅ 可用 |

### 3.2 不可用因子的处理策略

EPIBS、EGIBS、EGIBS_s 依赖分析师一致预期数据，Tushare 未提供。

**替代方案:**
- **Earnings Yield**: 权重重分配 → `0.00·EPIBS + 0.34·ETOP + 0.66·CETOP`
  (按原始 ETOP:CETOP = 11:21 比例归一化)
- **Growth**: 权重重分配 → `0.43·SGRO + 0.57·EGRO`
  (按原始 SGRO:EGRO = 18:24 比例归一化)


---

## 4. 因子计算模块设计

### 4.1 文件: `data-source/tushare/barra_cne5_factor_builder.py`

```
class BarraCNE5FactorBuilder:
    """
    职责: 从 tushare_data/ 读取原始数据，计算 CNE5 因子
    输入: tushare_data_path, universe (股票列表), date_range
    输出: DataFrame[ts_code, trade_date, factor_1, ..., factor_10]
    """

    def __init__(self, tushare_data_path: str):
        self._data_path = Path(tushare_data_path)
        self._daily_cache = {}       # ts_code → DataFrame
        self._fundamental_cache = {} # ts_code → DataFrame

    # ── 价格因子 (Phase 1) ──────────────────────────────

    def compute_beta(self, ts_code, trade_date) -> dict[str, float]:
        """
        BETA + HSIGMA (共享回归)
        1. 读取 daily[ts_code] 最近 252 天收益率
        2. 读取 index_daily[000300.SH] 同期收益率
        3. 读取 shibor 1Y 利率 → 日化无风险收益率
        4. 指数加权回归 (half-life=63)
        5. 返回 {'beta': β, 'hsigma': std(residuals)}
        """

    def compute_rstr(self, ts_code, trade_date) -> float:
        """
        RSTR (动量)
        1. 读取 daily[ts_code] 最近 504+21 天收益率
        2. 跳过最近 21 天 (lag)
        3. 计算超额对数收益率
        4. 指数加权求和 (half-life=126)
        """

    def compute_lncap(self, ts_code, trade_date) -> float:
        """
        LNCAP (规模)
        1. 读取 daily_basic[ts_code] 当日 total_mv
        2. 返回 ln(total_mv)
        注意: total_mv 单位为万元，需统一
        """

    def compute_dastd(self, ts_code, trade_date) -> float:
        """
        DASTD (日波动率)
        1. 读取 daily[ts_code] 最近 252 天超额收益率
        2. 指数加权标准差 (half-life=42)
        """

    def compute_cmra(self, ts_code, trade_date) -> float:
        """
        CMRA (累积区间)
        1. 读取 daily[ts_code] 最近 252 天收益率
        2. 按21天分月，计算累积超额对数收益 Z(T), T=1..12
        3. CMRA = max(Z) - min(Z)
        """

    def compute_liquidity(self, ts_code, trade_date) -> dict[str, float]:
        """
        STOM, STOQ, STOA
        1. 读取 daily[ts_code] 成交量 (vol)
        2. 读取 daily_basic[ts_code] 流通股数 (float_share)
        3. STOM = ln(Σ vol_21d / float_share)
        4. STOQ = ln(Σ vol_63d / float_share)
        5. STOA = ln(Σ vol_252d / float_share)
        """

    # ── 基本面因子 (Phase 2) ──────────────────────────────

    def compute_etop(self, ts_code, trade_date) -> float:
        """
        ETOP (盈利收益率)
        1. 读取 income[ts_code] → TTM净利润
           TTM = 最近年报 + (最新季报 - 去年同期季报)
        2. 读取 daily_basic[ts_code] → total_mv
        3. ETOP = TTM净利润 / total_mv
        注意: 使用 f_ann_date 避免前视偏差
        """

    def compute_cetop(self, ts_code, trade_date) -> float:
        """
        CETOP (现金盈利收益率)
        1. TTM净利润 (同 ETOP)
        2. TTM折旧摊销 = depr_fa_coga_dpba + amort_intang_assets
        3. 现金盈利 = 净利润 + 折旧摊销
        4. CETOP = 现金盈利 / total_mv
        """

    def compute_btop(self, ts_code, trade_date) -> float:
        """
        BTOP (账面市值比)
        1. 读取 balancesheet[ts_code] → total_hldr_eqy_exc_min_int
        2. 读取 daily_basic[ts_code] → total_mv
        3. BTOP = 账面权益 / total_mv
        注意: 使用 f_ann_date 避免前视偏差
        """

    def compute_leverage(self, ts_code, trade_date) -> dict[str, float]:
        """
        MLEV, DTOA, BLEV
        1. 读取 balancesheet → lt_borr, st_borr, total_assets, total_liab,
                               total_hldr_eqy_exc_min_int
        2. 读取 daily_basic → total_mv
        3. MLEV = (total_mv + lt_borr + st_borr) / total_mv
        4. DTOA = total_liab / total_assets
        5. BLEV = (book_equity + lt_borr + st_borr) / book_equity
        """

    def compute_growth(self, ts_code, trade_date) -> dict[str, float]:
        """
        SGRO, EGRO
        1. 读取 income[ts_code] 最近5年年报 revenue, n_income_attr_p
        2. 对 ln(revenue) 做时间回归 → 斜率 = SGRO
        3. 对 ln(n_income_attr_p) 做时间回归 → 斜率 = EGRO
        最少需要3年数据
        """

    def compute_nlsize(self, lncap_series: pd.Series) -> pd.Series:
        """
        NLSIZE (非线性规模) — 截面计算
        1. cube = lncap³
        2. 对 lncap 做 OLS 回归: cube ~ α + β·lncap
        3. NLSIZE = 残差
        """

    # ── 组合计算 ──────────────────────────────────────────

    def build_factor_snapshot(self, universe: list[str], trade_date: str) -> pd.DataFrame:
        """
        主入口: 计算某日全部因子
        1. 遍历 universe，计算每只股票的21个描述子
        2. 组合为10个风格因子 (加权求和)
        3. 截面标准化 (winsorize 3σ + z-score)
        4. 正交化 (ResVol vs Beta)
        5. 返回 DataFrame
        """
```

### 4.2 因子组合权重

```python
FACTOR_COMPOSITION = {
    'beta':       {'BETA': 1.00},
    'momentum':   {'RSTR': 1.00},
    'size':       {'LNCAP': 1.00},
    'earnyld':    {'ETOP': 0.34, 'CETOP': 0.66},       # 无EPIBS，重分配
    'resvol':     {'DASTD': 0.74, 'CMRA': 0.16, 'HSIGMA': 0.10},
    'growth':     {'SGRO': 0.43, 'EGRO': 0.57},         # 无EGIBS，重分配
    'btop':       {'BTOP': 1.00},
    'leverage':   {'MLEV': 0.38, 'DTOA': 0.35, 'BLEV': 0.27},
    'liquidity':  {'STOM': 0.35, 'STOQ': 0.35, 'STOA': 0.30},
    'nlsize':     {'NLSIZE': 1.00},
}
```

### 4.3 标准化流程

```
原始描述子值
    ↓
Winsorize (截断 ±3σ 以外的极端值)
    ↓
Z-Score 标准化 (均值0, 标准差1)
    ↓
加权组合为风格因子
    ↓
因子级 Z-Score 标准化
    ↓
正交化 (ResVol 对 Beta 回归取残差)
    ↓
最终因子值
```


---

## 5. 数据流转详细设计

### 5.1 离线因子计算流程

```
tushare_data/                          Data/alternative/barra-cne5-factors/
├── daily/date=000001.SZ/              ├── 000001.SZ/
│   └── *.parquet                      │   └── factors.csv
├── daily_basic/date=000001.SZ/        ├── 000002.SZ/
│   └── *.parquet                      │   └── factors.csv
├── income/date=000001.SZ/             └── ...
│   └── *.parquet                      
├── balancesheet/date=000001.SZ/       factors.csv 格式:
│   └── *.parquet                      ┌──────────┬──────┬──────────┬─────┬───────────┐
├── cashflow/date=000001.SZ/           │trade_date│ beta │ momentum │size │ earnyld   │
│   └── *.parquet                      ├──────────┼──────┼──────────┼─────┼───────────┤
├── index_daily/ts_code=000300.SH/     │ 20240102 │ 1.23 │  -0.45   │2.31 │  0.67     │
│   └── *.parquet                      │ 20240103 │ 1.21 │  -0.42   │2.30 │  0.65     │
└── shibor/year=2024/                  └──────────┴──────┴──────────┴─────┴───────────┘
    └── *.parquet                      (续) resvol, growth, btop, leverage, liquidity, nlsize
```

### 5.2 LEAN 数据读取流程

```
LEAN Engine 启动
    ↓
AShareBarraCNE5Algorithm.Initialize()
    ├─ 读取 config.json → parameters
    ├─ 加载股票 universe (沪深300成分股)
    ├─ 为每只股票注册 AShareBarraCNE5FactorData 自定义数据源
    └─ 设置交易参数 (T+1, 费率, 仓位限制)
    ↓
每个交易日 OnData(Slice data)
    ├─ 收集所有股票的最新因子数据
    ├─ AShareBarraCNE5SignalModel.ComputeScores()
    │   ├─ 多因子加权评分
    │   ├─ 排序选股 (Top N / Bottom N)
    │   └─ 生成目标持仓权重
    ├─ 执行调仓
    │   ├─ 计算当前持仓 vs 目标持仓差异
    │   ├─ 生成买卖订单
    │   └─ 输出调仓明细 (友好格式)
    └─ 记录交易日志
    ↓
Results/
    ├─ barra-cne5-trades.csv           # 交易记录
    ├─ barra-cne5-daily-summary.csv    # 每日P&L
    ├─ barra-cne5-allocation.csv       # 持仓分配
    └─ barra-cne5-factor-exposure.csv  # 因子暴露度
```

### 5.3 蒙特卡洛回测流程

```
基础回测完成 → Results/barra-cne5-daily-summary.csv
    ↓
Scripts/barra_cne5_monte_carlo.py
    ↓
┌─────────────────────────────────────────────────────┐
│  方法1: 收益率 Bootstrap                              │
│  1. 读取日收益率序列 r_1, r_2, ..., r_T              │
│  2. 有放回抽样生成 N 条模拟路径                        │
│  3. 每条路径计算: 累积收益、最大回撤、夏普比率          │
│  4. 统计 N 条路径的分布                               │
├─────────────────────────────────────────────────────┤
│  方法2: 因子扰动 (Factor Perturbation)               │
│  1. 读取历史因子暴露矩阵 F[T×K]                      │
│  2. 估计因子协方差矩阵 Σ_F                           │
│  3. 对因子收益添加随机扰动: f' = f + ε, ε~N(0,σ²)   │
│  4. 重新计算组合收益                                  │
│  5. 统计 N 次模拟的分布                               │
├─────────────────────────────────────────────────────┤
│  方法3: Block Bootstrap                              │
│  1. 将收益率序列分为 L 天的块                         │
│  2. 有放回抽样块 → 拼接为模拟路径                     │
│  3. 保留收益率的自相关结构                            │
└─────────────────────────────────────────────────────┘
    ↓
输出报告:
┌────────────────────────────────────────────────────────────────┐
│  📊 MONTE CARLO SIMULATION REPORT                              │
│  ══════════════════════════════════════════════════════════════ │
│  Simulations: 10,000 | Method: Block Bootstrap (L=21)          │
│                                                                │
│  📈 Annualized Return Distribution                             │
│  ────────────────────────────────────────────────────────────  │
│    5th percentile:   +3.2%                                     │
│   25th percentile:  +12.8%                                     │
│   50th percentile:  +18.5%  (median)                           │
│   75th percentile:  +24.1%                                     │
│   95th percentile:  +35.7%                                     │
│                                                                │
│  📉 Maximum Drawdown Distribution                              │
│  ────────────────────────────────────────────────────────────  │
│    5th percentile:  -32.1%  (worst case)                       │
│   50th percentile:  -15.3%                                     │
│   95th percentile:   -6.2%  (best case)                        │
│                                                                │
│  📊 Sharpe Ratio Distribution                                  │
│  ────────────────────────────────────────────────────────────  │
│    5th percentile:   0.35                                      │
│   50th percentile:   1.12                                      │
│   95th percentile:   1.89                                      │
│                                                                │
│  ⚠️  Value at Risk (95%):  -2.3% daily                         │
│  ⚠️  Prob(negative return):  12.3%                              │
└────────────────────────────────────────────────────────────────┘
```


---

## 6. 文件清单与职责

### 6.1 新增文件

| 文件路径 | 层级 | 职责 | 预估行数 |
|---|---|---|---|
| `data-source/tushare/barra_cne5_factor_builder.py` | Data | 21个描述子计算 + 10个因子组合 | ~600 |
| `data-source/tushare/barra_cne5_data_loader.py` | Data | Tushare parquet 数据加载器 | ~200 |
| `Scripts/barra_cne5_factor_bridge.py` | Data | 因子计算调度 + 终端输出 | ~300 |
| `Scripts/barra_cne5_monte_carlo.py` | Analysis | 蒙特卡洛模拟 + 报告生成 | ~350 |
| `Algorithm.CSharp/AShareBarraCNE5FactorData.cs` | Algorithm | LEAN 自定义数据 (读取 factors.csv) | ~150 |
| `Algorithm.CSharp/AShareBarraCNE5SignalModel.cs` | Algorithm | 多因子评分 + 选股逻辑 | ~200 |
| `Algorithm.CSharp/AShareBarraCNE5Algorithm.cs` | Algorithm | 交易算法主体 | ~350 |
| `Launcher/config/config-barra-cne5-backtest.json` | Config | 回测配置 | ~50 |
| `Tests/barra_cne5/test_factor_builder.py` | Test | 因子计算单元测试 | ~400 |
| `Tests/barra_cne5/test_data_loader.py` | Test | 数据加载测试 | ~150 |
| `Tests/barra_cne5/test_monte_carlo.py` | Test | 蒙特卡洛模拟测试 | ~200 |

### 6.2 不修改的文件

现有 T0 ETF 策略文件完全不受影响:
- `AShareEtfT0FeatureData.cs` — 不修改
- `AShareEtfT0FeatureSignalModel.cs` — 不修改
- `AShareEtfT0FeatureIntradayAlgorithm.cs` — 不修改
- `Scripts/ashare_etf_t0_feature_live_bridge.py` — 不修改

---

## 7. C# Algorithm 层详细设计

### 7.1 AShareBarraCNE5FactorData.cs

```csharp
// 自定义数据类，读取 Python 层输出的 factors.csv
public class AShareBarraCNE5FactorData : BaseData
{
    // 10 个风格因子
    public decimal Beta { get; set; }
    public decimal Momentum { get; set; }
    public decimal Size { get; set; }
    public decimal EarningsYield { get; set; }
    public decimal ResidualVolatility { get; set; }
    public decimal Growth { get; set; }
    public decimal BookToPrice { get; set; }
    public decimal Leverage { get; set; }
    public decimal Liquidity { get; set; }
    public decimal NonLinearSize { get; set; }

    // 辅助字段
    public decimal TotalMv { get; set; }      // 用于市值加权
    public decimal TurnoverRate { get; set; }  // 用于流动性过滤

    public override SubscriptionDataSource GetSource(...)
    {
        // 读取 Data/alternative/barra-cne5-factors/{symbol}/factors.csv
    }

    public override BaseData Reader(...)
    {
        // 解析 CSV 行 → AShareBarraCNE5FactorData
    }
}
```

### 7.2 AShareBarraCNE5SignalModel.cs

```csharp
public static class AShareBarraCNE5SignalModel
{
    // 因子权重 (可通过 config.json parameters 覆盖)
    public static readonly Dictionary<string, decimal> DefaultWeights = new()
    {
        ["Beta"]               = -0.05m,  // 低Beta偏好
        ["Momentum"]           =  0.20m,  // 动量正向
        ["Size"]               = -0.10m,  // 小盘偏好
        ["EarningsYield"]      =  0.20m,  // 高盈利偏好
        ["ResidualVolatility"] = -0.10m,  // 低波动偏好
        ["Growth"]             =  0.15m,  // 高成长偏好
        ["BookToPrice"]        =  0.10m,  // 价值偏好
        ["Leverage"]           = -0.05m,  // 低杠杆偏好
        ["Liquidity"]          =  0.05m,  // 适度流动性
        ["NonLinearSize"]      =  0.00m,  // 默认不用
    };

    public static Dictionary<Symbol, decimal> ComputeScores(
        IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> factors,
        IReadOnlyDictionary<string, decimal> weights)
    {
        // 1. 截面 z-score 标准化每个因子
        // 2. 加权求和 → 综合得分
        // 3. 返回 {symbol: score}
    }

    public static List<(Symbol symbol, decimal weight)> SelectPortfolio(
        Dictionary<Symbol, decimal> scores,
        int topN,
        decimal minScoreSpread)
    {
        // 1. 按 score 降序排列
        // 2. 检查 top vs bottom 的 spread
        // 3. 选择 Top N，等权或市值加权
        // 4. 返回目标持仓
    }
}
```

### 7.3 AShareBarraCNE5Algorithm.cs

```csharp
public class AShareBarraCNE5Algorithm : QCAlgorithm
{
    public override void Initialize()
    {
        // 1. 设置回测区间 (从 config 读取)
        // 2. 设置初始资金 (1,000,000 CNY)
        // 3. 加载 universe (沪深300 或全A)
        // 4. 注册 AShareBarraCNE5FactorData 数据源
        // 5. 设置 T+1 settlement model
        // 6. 设置 A股费率模型
        // 7. 设置调仓频率 (月度/双周)
    }

    public override void OnData(Slice data)
    {
        // 1. 收集最新因子数据
        // 2. 过滤: 停牌、ST、上市不足1年
        // 3. 调用 SignalModel.ComputeScores()
        // 4. 调用 SignalModel.SelectPortfolio()
        // 5. 执行调仓 (SetHoldings)
        // 6. 输出调仓明细
        // 7. 记录因子暴露度
    }

    // 友好输出: 每次调仓打印
    // ┌──────────────────────────────────────────────────┐
    // │ 📊 REBALANCE: 2024-03-15                         │
    // │ ─────────────────────────────────────────────────│
    // │ 🟢 BUY:  000001.SZ  平安银行  Weight: 3.33%     │
    // │ 🟢 BUY:  600519.SH  贵州茅台  Weight: 3.33%     │
    // │ 🔴 SELL: 000002.SZ  万科A     Weight: 0.00%     │
    // │ ─────────────────────────────────────────────────│
    // │ Portfolio: 30 stocks | Exposure: 95.0%           │
    // │ Factor Tilt: Mom +0.45 | Val +0.32 | Size -0.28 │
    // └──────────────────────────────────────────────────┘
}
```


---

## 8. 蒙特卡洛回测详细设计

### 8.1 文件: `Scripts/barra_cne5_monte_carlo.py`

```python
class BarraCNE5MonteCarlo:
    """
    蒙特卡洛模拟引擎
    输入: 基础回测的日收益率序列 + 因子暴露矩阵
    输出: 模拟统计报告
    """

    def __init__(self, config: dict):
        self.n_simulations = config.get('mc-simulations', 10000)
        self.block_size = config.get('mc-block-size', 21)  # 21天=1月
        self.confidence_levels = [0.05, 0.25, 0.50, 0.75, 0.95]

    def run_block_bootstrap(self, daily_returns: np.ndarray) -> SimulationResult:
        """
        Block Bootstrap 方法 (主方法)
        1. 将 daily_returns 分为 block_size 天的块
        2. 有放回抽样块，拼接为与原序列等长的模拟路径
        3. 重复 n_simulations 次
        4. 每条路径计算:
           - 累积收益率
           - 年化收益率
           - 最大回撤
           - 夏普比率
           - Calmar比率
           - 95% VaR
        """

    def run_factor_perturbation(
        self,
        factor_returns: np.ndarray,    # [T, K] 因子收益矩阵
        factor_exposures: np.ndarray,  # [T, K] 组合因子暴露
        specific_returns: np.ndarray,  # [T] 特质收益
    ) -> SimulationResult:
        """
        因子扰动方法
        1. 估计因子收益协方差矩阵 Σ_F
        2. 对每次模拟:
           a. 生成扰动: ε ~ N(0, 0.1·Σ_F)
           b. 扰动后因子收益: f' = f + ε
           c. 组合收益: r' = exposure @ f' + specific_return
        3. 统计 N 次模拟结果
        """

    def run_parametric(self, daily_returns: np.ndarray) -> SimulationResult:
        """
        参数化方法 (辅助验证)
        1. 估计收益率分布参数 (均值μ, 标准差σ, 偏度, 峰度)
        2. 使用 Johnson SU 分布拟合
        3. 从拟合分布中抽样
        """

    def generate_report(self, result: SimulationResult) -> str:
        """生成友好的终端报告"""

    def export_results(self, result: SimulationResult, output_path: str):
        """导出详细结果到 CSV + JSON"""
```

### 8.2 SimulationResult 数据结构

```python
@dataclass(frozen=True)
class SimulationResult:
    method: str                          # 'block_bootstrap' | 'factor_perturbation'
    n_simulations: int
    annual_returns: np.ndarray           # [N] 年化收益率
    max_drawdowns: np.ndarray            # [N] 最大回撤
    sharpe_ratios: np.ndarray            # [N] 夏普比率
    calmar_ratios: np.ndarray            # [N] Calmar比率
    var_95: float                        # 95% VaR (日)
    cvar_95: float                       # 95% CVaR (日)
    prob_negative: float                 # 负收益概率
    cumulative_paths: np.ndarray         # [N, T] 累积收益路径 (可选，用于绘图)
```

---

## 9. 终端输出设计

### 9.1 因子计算阶段

```
================================================================================
🔄 Building Barra CNE5 Factors: 2024-03-15
📅 Universe: 300 stocks (CSI 300) | Lookback: 504 days
================================================================================

📊 Phase 1: Price Factors (5/5)
────────────────────────────────────────────────────────────────────────────────
  [1/5] BETA + HSIGMA .......... ✅ 298/300 computed (2 insufficient data)
  [2/5] RSTR (Momentum) ....... ✅ 295/300 computed (5 insufficient history)
  [3/5] LNCAP (Size) .......... ✅ 300/300 computed
  [4/5] DASTD + CMRA .......... ✅ 298/300 computed
  [5/5] STOM + STOQ + STOA .... ✅ 300/300 computed

📊 Phase 2: Fundamental Factors (4/4)
────────────────────────────────────────────────────────────────────────────────
  [1/4] ETOP + CETOP .......... ✅ 285/300 computed (15 missing financials)
  [2/4] BTOP .................. ✅ 290/300 computed
  [3/4] MLEV + DTOA + BLEV .... ✅ 288/300 computed
  [4/4] SGRO + EGRO ........... ✅ 270/300 computed (30 < 3yr history)

📊 Phase 3: Composite Factors
────────────────────────────────────────────────────────────────────────────────
  Standardization ............. ✅ Winsorize ±3σ → Z-Score
  Orthogonalization ........... ✅ ResVol ⊥ Beta (R²=0.00)
  Non-linear Size ............. ✅ NLSIZE = LNCAP³ ⊥ LNCAP

📊 Factor Statistics (Cross-Section)
────────────────────────────────────────────────────────────────────────────────
  Factor      |  Mean  |  Std  |  Min   |  Max   | Coverage
  ────────────┼────────┼───────┼────────┼────────┼─────────
  Beta        |  0.00  | 1.00  | -2.87  |  2.95  |  99.3%
  Momentum    |  0.00  | 1.00  | -2.64  |  2.98  |  98.3%
  Size        |  0.00  | 1.00  | -2.45  |  3.00  | 100.0%
  EarningsYld |  0.00  | 1.00  | -2.91  |  2.88  |  95.0%
  ResidVol    |  0.00  | 1.00  | -2.78  |  2.99  |  99.3%
  Growth      |  0.00  | 1.00  | -2.55  |  2.93  |  90.0%
  BookToPrice |  0.00  | 1.00  | -2.82  |  2.97  |  96.7%
  Leverage    |  0.00  | 1.00  | -2.90  |  2.95  |  96.0%
  Liquidity   |  0.00  | 1.00  | -2.71  |  2.99  | 100.0%
  NLSize      |  0.00  | 1.00  | -2.60  |  2.96  | 100.0%

✅ Written: 300 stocks → Data/alternative/barra-cne5-factors/
================================================================================
```

### 9.2 回测阶段 (LEAN 算法输出)

```
====================================================================================================
📊 BARRA CNE5 REBALANCE — 2024-03-15
====================================================================================================

🔍 Scoring 285 eligible stocks (15 filtered: 8 suspended, 5 ST, 2 new listing)

📈 TOP 30 STOCKS
────────────────────────────────────────────────────────────────────────────────────────────────────
  Rank | Code       | Name     | Score  | Beta  | Mom   | Size  | EY    | Weight
────────────────────────────────────────────────────────────────────────────────────────────────────
     1 | 600519.SH  | 贵州茅台 | +1.82  | -0.32 | +1.45 | +2.10 | +0.89 |  3.33%
     2 | 000858.SZ  | 五粮液   | +1.65  | -0.28 | +1.32 | +1.85 | +0.76 |  3.33%
     3 | 601318.SH  | 中国平安 | +1.58  | -0.15 | +0.98 | +2.45 | +1.12 |  3.33%
  ...

📊 PORTFOLIO SUMMARY
────────────────────────────────────────────────────────────────────────────────────────────────────
  Holdings: 30 stocks | Turnover: 23.5% | Exposure: 95.0%
  Capital: ¥1,000,000 | Invested: ¥950,000 | Cash: ¥50,000

📊 FACTOR EXPOSURE (Portfolio vs Benchmark)
────────────────────────────────────────────────────────────────────────────────────────────────────
  Factor      | Portfolio | Benchmark | Active
  ────────────┼───────────┼───────────┼────────
  Beta        |    -0.25  |     0.00  |  -0.25
  Momentum    |    +0.45  |     0.00  |  +0.45
  Size        |    -0.28  |     0.00  |  -0.28
  EarningsYld |    +0.32  |     0.00  |  +0.32
  ResidVol    |    -0.18  |     0.00  |  -0.18
  Growth      |    +0.22  |     0.00  |  +0.22
  BookToPrice |    +0.15  |     0.00  |  +0.15
  Leverage    |    -0.12  |     0.00  |  -0.12
  Liquidity   |    +0.08  |     0.00  |  +0.08
  NLSize      |    +0.03  |     0.00  |  +0.03
====================================================================================================
```

### 9.3 蒙特卡洛报告

```
====================================================================================================
📊 MONTE CARLO SIMULATION REPORT
====================================================================================================
  Method: Block Bootstrap (block=21 days) | Simulations: 10,000
  Base Period: 2020-01-02 → 2024-12-31 (1,215 trading days)
────────────────────────────────────────────────────────────────────────────────────────────────────

📈 ANNUALIZED RETURN
────────────────────────────────────────────────────────────────────────────────────────────────────
   5th pctl │██                                          │  +3.2%
  25th pctl │████████                                    │ +12.8%
  50th pctl │████████████                                │ +18.5%  ← median
  75th pctl │████████████████                            │ +24.1%
  95th pctl │██████████████████████████                  │ +35.7%

📉 MAXIMUM DRAWDOWN
────────────────────────────────────────────────────────────────────────────────────────────────────
   5th pctl │██████████████████████████████████          │ -32.1%  ← worst
  25th pctl │██████████████████████                      │ -21.5%
  50th pctl │████████████████                            │ -15.3%
  75th pctl │██████████                                  │ -10.2%
  95th pctl │██████                                      │  -6.2%

📊 SHARPE RATIO
────────────────────────────────────────────────────────────────────────────────────────────────────
   5th pctl │████                                        │  0.35
  50th pctl │████████████                                │  1.12
  95th pctl │████████████████████                        │  1.89

⚠️  RISK METRICS
────────────────────────────────────────────────────────────────────────────────────────────────────
  Daily VaR (95%):     -2.3%
  Daily CVaR (95%):    -3.1%
  Prob(annual < 0%):   12.3%
  Prob(drawdown > 30%): 5.8%

✅ Results saved to: Results/barra-cne5-monte-carlo-report.json
====================================================================================================
```


---

## 10. 任务分工与实施计划

### Phase 1: 数据层 — 因子计算 (TDD)

| 任务ID | 任务 | 依赖 | 预估 |
|---|---|---|---|
| D1 | `barra_cne5_data_loader.py` — Tushare parquet 数据加载器 | 无 | 小 |
| D1-T | `test_data_loader.py` — 数据加载单元测试 (先写) | 无 | 小 |
| D2 | `barra_cne5_factor_builder.py` — 价格因子 (BETA, RSTR, LNCAP, DASTD, CMRA, HSIGMA, STOM/Q/A) | D1 | 中 |
| D2-T | `test_factor_builder.py` — 价格因子测试 (先写) | D1-T | 中 |
| D3 | `barra_cne5_factor_builder.py` — 基本面因子 (ETOP, CETOP, BTOP, MLEV, DTOA, BLEV, SGRO, EGRO) | D1 | 中 |
| D3-T | `test_factor_builder.py` — 基本面因子测试 (先写) | D1-T | 中 |
| D4 | `barra_cne5_factor_builder.py` — 标准化 + 正交化 + NLSIZE | D2, D3 | 小 |
| D4-T | `test_factor_builder.py` — 标准化测试 (先写) | D2-T | 小 |
| D5 | `barra_cne5_factor_bridge.py` — 调度脚本 + 终端输出 | D4 | 小 |

### Phase 2: 算法层 — LEAN 集成

| 任务ID | 任务 | 依赖 | 预估 |
|---|---|---|---|
| A1 | `AShareBarraCNE5FactorData.cs` — 自定义数据类 | D4 (输出格式确定) | 小 |
| A2 | `AShareBarraCNE5SignalModel.cs` — 多因子评分模型 | A1 | 中 |
| A3 | `AShareBarraCNE5Algorithm.cs` — 交易算法 + 友好输出 | A1, A2 | 中 |
| A4 | `config-barra-cne5-backtest.json` — 回测配置 | A3 | 小 |

### Phase 3: 蒙特卡洛模拟

| 任务ID | 任务 | 依赖 | 预估 |
|---|---|---|---|
| M1 | `barra_cne5_monte_carlo.py` — Block Bootstrap | A3 (回测结果) | 中 |
| M1-T | `test_monte_carlo.py` — Bootstrap 测试 (先写) | 无 | 小 |
| M2 | `barra_cne5_monte_carlo.py` — 因子扰动方法 | M1 | 中 |
| M3 | `barra_cne5_monte_carlo.py` — 报告生成 + 终端输出 | M1, M2 | 小 |

### Phase 4: 集成验证

| 任务ID | 任务 | 依赖 | 预估 |
|---|---|---|---|
| V1 | 端到端测试: tushare_data → 因子 → LEAN回测 → 蒙特卡洛 | 全部 | 中 |
| V2 | 因子有效性验证: IC/IR 分析 | D4 | 小 |
| V3 | 性能优化: 大规模股票池计算效率 | V1 | 小 |

### 执行顺序 (TDD)

```
D1-T → D1 → D2-T → D2 → D3-T → D3 → D4-T → D4 → D5
                                                  ↓
                              A1 → A2 → A3 → A4 → 回测运行
                                                  ↓
                              M1-T → M1 → M2 → M3 → 蒙特卡洛报告
                                                  ↓
                                          V1 → V2 → V3
```

---

## 11. 配置文件设计

### `Launcher/config/config-barra-cne5-backtest.json`

```json
{
  "environment": "backtesting",
  "algorithm-type-name": "AShareBarraCNE5Algorithm",
  "algorithm-language": "CSharp",
  "algorithm-location": "../../../Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll",
  "data-folder": "../../../Data",
  "results-destination-folder": "../../../Results",
  "parameters": {
    "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
    "factor-data-path": "../../../Data/alternative/barra-cne5-factors",
    "universe": "csi300",
    "start-date": "20200101",
    "end-date": "20251231",
    "initial-cash": "1000000",
    "rebalance-frequency": "monthly",
    "top-n": "30",
    "min-score-spread": "0.5",
    "target-portfolio-exposure": "0.95",
    "settlement-days": "1",
    "factor-weight-beta": "-0.05",
    "factor-weight-momentum": "0.20",
    "factor-weight-size": "-0.10",
    "factor-weight-earnyld": "0.20",
    "factor-weight-resvol": "-0.10",
    "factor-weight-growth": "0.15",
    "factor-weight-btop": "0.10",
    "factor-weight-leverage": "-0.05",
    "factor-weight-liquidity": "0.05",
    "factor-weight-nlsize": "0.00",
    "trade-report-file": "barra-cne5-trades.csv",
    "daily-summary-file": "barra-cne5-daily-summary.csv",
    "allocation-report-file": "barra-cne5-allocation.csv",
    "factor-exposure-file": "barra-cne5-factor-exposure.csv",
    "mc-simulations": "10000",
    "mc-block-size": "21",
    "mc-confidence-levels": "0.05,0.25,0.50,0.75,0.95"
  }
}
```

---

## 12. 关键设计决策

### 12.1 为什么用 Python 计算因子，C# 做交易？

- **因子计算** 涉及大量矩阵运算、回归分析、统计处理 → Python (numpy/pandas/scipy) 更高效
- **交易执行** 需要 LEAN 的事件驱动引擎、订单管理、风控 → C# 是 LEAN 原生语言
- **数据桥接** 通过 CSV 文件，简单可靠，与现有 T0 ETF 策略模式一致

### 12.2 为什么选 Block Bootstrap 作为主要蒙特卡洛方法？

- 保留收益率的自相关结构（普通 bootstrap 会破坏）
- 不需要假设收益率分布（参数化方法需要）
- 实现简单，统计性质良好
- 21天块大小 ≈ 1个月，与调仓频率匹配

### 12.3 前视偏差防护

- 财务数据使用 `f_ann_date`（实际公告日）而非 `end_date`（报告期末）
- 因子计算只使用 trade_date 之前已公告的数据
- 分析师预期数据不可用，避免了最常见的前视偏差来源

### 12.4 缺失值处理策略

- 价格因子: 历史数据不足 → 该描述子设为 NaN → 因子组合时跳过
- 基本面因子: 财报未公告 → 使用最近一期已公告数据
- 截面标准化: NaN 不参与均值/标准差计算，标准化后仍为 NaN
- 选股: NaN 因子数 > 3 的股票排除出 universe

---

## 13. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| 分析师预期数据缺失 | Earnings Yield 和 Growth 因子不完整 | 权重重分配到可用描述子 |
| 财务数据延迟 | 前视偏差 | 严格使用 f_ann_date |
| 小盘股数据质量差 | 因子计算异常 | Winsorize + 最小市值过滤 |
| 计算耗时 (5000+股票 × 504天) | 因子构建慢 | 缓存机制 + 增量计算 |
| 蒙特卡洛模拟量大 | 内存/时间 | numpy 向量化 + 可配置模拟次数 |

---

## 14. 使用方式

### 14.1 完整流程

```bash
# Step 1: 计算因子 (Python)
cd /home/project/hope/Lean
python Scripts/barra_cne5_factor_bridge.py \
  --tushare-data /home/project/tushare-downloader/tushare_data \
  --universe csi300 \
  --start-date 20200101 \
  --end-date 20251231

# Step 2: 运行回测 (LEAN)
dotnet run --project Launcher \
  --config Launcher/config/config-barra-cne5-backtest.json

# Step 3: 蒙特卡洛模拟 (Python)
python Scripts/barra_cne5_monte_carlo.py \
  --results Results/barra-cne5-daily-summary.csv \
  --factor-exposure Results/barra-cne5-factor-exposure.csv \
  --simulations 10000
```

### 14.2 快速验证

```bash
# 单日因子计算测试
python Scripts/barra_cne5_factor_bridge.py \
  --tushare-data /home/project/tushare-downloader/tushare_data \
  --universe csi300 \
  --date 20241231 \
  --once

# 运行测试
python -m pytest Tests/barra_cne5/ -v
```

