# Data Plus: 补充数据源文档

本文档记录 tushare_data 以外的补充数据，包括免费下载的真实数据和仿真数据，用于支撑量化策略回测。

## 数据目录结构

```
local_data/
├── risk_free_rate/
│   └── shibor_1y_risk_free.csv          # 无风险利率
├── industry_classification/
│   ├── ashare_sw31_classification.csv    # 全A股申万31行业分类
│   └── sw_l1_daily_returns.csv           # 申万一级行业日行情
├── analyst_estimates/
│   └── synthetic_consensus_estimates.csv # 仿真分析师一致预期
├── bond_yield_curve/
│   └── synthetic_cgb_yield_curve.csv     # 仿真国债收益率曲线
├── computed_quality_factors.csv          # 计算的质量因子
└── ashare-sector-smallcap-factors.csv    # 小盘因子数据
```

---

## 1. 无风险利率 (Risk-Free Rate)

### 数据源: Shibor 1Y 利率

| 属性 | 值 |
|------|-----|
| 文件 | `local_data/risk_free_rate/shibor_1y_risk_free.csv` |
| 来源 | tushare `shibor` 接口 (真实数据) |
| 费用 | 免费 (tushare 基础积分即可) |
| 时间范围 | 2006-10 ~ 2026-01 |
| 行数 | 4,801 |
| 字段 | `trade_date`, `risk_free_rate_1y`, `risk_free_rate_daily` |

**字段说明:**
- `risk_free_rate_1y`: 1年期 Shibor 利率 (小数形式, 如 0.0295 = 2.95%)
- `risk_free_rate_daily`: 日化无风险利率 = 1y利率 / 252

**用途:**
- Barra CNE5 因子模型: Beta, RSTR (动量), DASTD/CMRA/HSIGMA (残差波动率) 的超额收益计算
- Sharpe 比率计算
- 行业中性化组合构建

**使用方式:**
```python
import pandas as pd
rf = pd.read_csv('local_data/risk_free_rate/shibor_1y_risk_free.csv')
daily_rf = rf.set_index('trade_date')['risk_free_rate_daily']
```

**注意事项:**
- Shibor 是银行间同业拆借利率，非国债收益率，但作为 A 股无风险利率代理已被广泛使用
- 2026-01-23 之后的数据需要更新 (可通过 tushare 重新下载)

---

## 2. 行业分类 (Industry Classification)

### 2.1 全A股申万31行业分类

| 属性 | 值 |
|------|-----|
| 文件 | `local_data/industry_classification/ashare_sw31_classification.csv` |
| 来源 | tushare `index_member_all` (3,000 股) + `stock_basic.industry` 映射 (2,473 股) |
| 费用 | 免费 |
| 行数 | 5,473 |
| 覆盖率 | 93.7% 精确映射到 SW31 L1, 6.3% 归入"综合" |

**字段说明:**
- `ts_code`: 股票代码
- `name`: 股票名称
- `tushare_industry`: tushare 原始行业分类 (110 个细分行业)
- `sw_l1_code`: 申万一级行业指数代码 (如 801040.SI)
- `sw_l1_name`: 申万一级行业名称 (31 个行业)
- `list_date`: 上市日期

**31 个申万一级行业:**
电子, 机械设备, 医药生物, 基础化工, 电力设备, 计算机, 汽车, 食品饮料, 有色金属,
轻工制造, 通信, 传媒, 交通运输, 环保, 纺织服饰, 公用事业, 家用电器, 农林牧渔,
建筑材料, 建筑装饰, 非银金融, 商贸零售, 房地产, 社会服务, 国防军工, 钢铁, 银行,
石油石化, 煤炭, 美容护理, 综合

**映射方法:**
1. 优先使用 tushare `index_member_all` 中的申万行业分类 (3,000 股精确映射)
2. 对不在 `index_member_all` 中的股票，将 `stock_basic.industry` (110 个细分行业) 映射到 SW31 L1
3. 无法映射的股票归入"综合" (801230.SI)

**用途:**
- 行业轮动策略
- 行业中性化组合构建
- Barra 风险模型行业因子
- SectorSmallCap 策略的行业分组

### 2.2 申万一级行业日行情

| 属性 | 值 |
|------|-----|
| 文件 | `local_data/industry_classification/sw_l1_daily_returns.csv` |
| 来源 | tushare `sw_daily` 接口 (真实数据) |
| 费用 | 免费 |
| 行数 | 38,098 |
| 时间范围 | 2000-11 ~ 2026-01 |
| 指数数量 | 289 (含 L1/L2/L3) |

**字段说明:**
- `ts_code`: 行业指数代码
- `trade_date`: 交易日期
- `name`: 行业名称
- `close`, `pct_change`: 收盘价和涨跌幅
- `pe`, `pb`: 行业估值
- `total_mv`: 行业总市值

**用途:**
- 行业动量/轮动信号
- 行业相对估值比较
- 行业中性化因子构建

---

## 3. 分析师一致预期 (Analyst Consensus Estimates)

### 数据源: 仿真数据

| 属性 | 值 |
|------|-----|
| 文件 | `local_data/analyst_estimates/synthetic_consensus_estimates.csv` |
| 来源 | 基于 `fina_indicator` 历史数据 + 统计模型生成 |
| 费用 | 仿真数据 (真实数据需付费) |
| 行数 | 2,000 |
| 覆盖股票 | 2,000 |

**字段说明:**
- `ts_code`: 股票代码
- `end_date`: 财报期
- `current_eps`: 当期实际 EPS
- `predicted_eps`: 分析师预测 EPS (含乐观偏差 +3%)
- `predicted_eps_growth`: 预测 EPS 增速
- `predicted_rev_growth`: 预测营收增速
- `n_analysts`: 覆盖分析师数量 (1-30)
- `eps_dispersion`: EPS 预测离散度

**仿真方法:**
1. 从 `fina_indicator` 提取历史 EPS 和营收增速
2. 分析师预测 EPS = 当前 EPS × (1 + 历史增速 + 乐观偏差 + 噪声)
3. 乐观偏差: N(0.03, 0.05) — 模拟分析师普遍的乐观倾向
4. 噪声: N(0, 0.02) — 模拟预测误差
5. 分析师数量: 对数正态分布, 均值约 6 人
6. 离散度: 与预测 EPS 成正比的随机值

**用途:**
- Barra CNE5 EPIBS (分析师预测收益) 描述因子
- Barra CNE5 EGIBS (分析师预测增长) 描述因子
- 盈余动量策略 (SUE - 标准化未预期盈余)

**⚠️ 重要提示:**
- 这是仿真数据, 不反映真实分析师预期
- 适用于策略逻辑验证和回测框架测试
- 实盘前需替换为真实分析师数据

**真实数据获取途径 (付费):**
| 来源 | 费用 | 说明 |
|------|------|------|
| Wind 万得 | ¥50,000+/年 | 最权威, 覆盖最全 |
| 东方财富 Choice | ¥10,000+/年 | 性价比较高 |
| 同花顺 iFinD | ¥15,000+/年 | 覆盖面广 |
| tushare `fina_forecast` | 需 2000+ 积分 | 业绩预告, 非一致预期 |

---

## 4. 国债收益率曲线 (Bond Yield Curve)

### 数据源: 仿真数据

| 属性 | 值 |
|------|-----|
| 文件 | `local_data/bond_yield_curve/synthetic_cgb_yield_curve.csv` |
| 来源 | 基于 Shibor 利率 + Nelson-Siegel 期限溢价模型 |
| 费用 | 仿真数据 (真实数据需付费) |
| 行数 | 1,260 |
| 时间范围 | 2020-12 ~ 2026-01 |

**字段说明:**
- `trade_date`: 交易日期
- `yield_1m` ~ `yield_30y`: 1个月到30年各期限收益率 (小数形式)

**仿真方法:**
1. 基准利率: Shibor 1Y (真实数据)
2. 短端 (1m/3m/6m): 直接使用 Shibor 对应期限
3. 长端 (2y/5y/10y/30y): 基准利率 + 期限溢价
   - 2y: +20bp (N(0.002, 0.001))
   - 5y: +60bp (N(0.006, 0.002))
   - 10y: +80bp (N(0.008, 0.003))
   - 30y: +90bp (N(0.009, 0.003))

**用途:**
- DCF 估值模型折现率
- WACC 计算 (ROIC vs WACC)
- 久期匹配策略
- 利率敏感度分析

**真实数据获取途径 (免费/付费):**
| 来源 | 费用 | 说明 |
|------|------|------|
| 中国债券信息网 (ChinaBond) | 免费注册 | 官方收益率曲线, 需手动下载 |
| tushare `yield_curve` | 需 5000+ 积分 | 国债收益率曲线 |
| Wind 万得 | ¥50,000+/年 | 最完整的期限结构数据 |

---

## 5. 计算的质量因子 (Computed Quality Factors)

### 数据源: 基于 tushare 原始财务数据计算

| 属性 | 值 |
|------|-----|
| 文件 | `local_data/computed_quality_factors.csv` |
| 来源 | `fina_indicator` 计算得出 |
| 费用 | 免费 |
| 行数 | 2,000 |
| 覆盖股票 | 500 |

**字段说明:**
- `ts_code`: 股票代码
- `end_date`: 财报期
- `ann_date`: 公告日期
- `roe`: 净资产收益率 (tushare 原始)
- `roa`: 总资产收益率 = ROE × (1 - 资产负债率/100)
- `roic`: 投入资本回报率 ≈ ROA × 1.3 (经验近似)
- `grossprofit_margin`: 毛利率 ≈ 净利率 × 2.5 (经验近似)
- `current_ratio`: 流动比率 ≈ (1-资产负债率/100) / (资产负债率/100) × 0.6
- `quick_ratio`: 速动比率 ≈ 流动比率 × 0.7
- `netprofit_margin`: 净利率 (tushare 原始)
- `debt_to_assets`: 资产负债率 (tushare 原始)
- `assets_turn`: 总资产周转率 (tushare 原始)
- `accruals_ratio`: 应计利润比率 = (EPS - OCPS) / |EPS|

**用途:**
- 质量因子策略 (Sloan 应计异常)
- 盈利能力筛选
- 财务健康度评估

**⚠️ 近似说明:**
- `roa`, `roic`, `grossprofit_margin`, `current_ratio`, `quick_ratio` 是基于经验关系的近似值
- 精确计算需要从 `income` + `balancesheet` 原始报表逐项计算
- `accruals_ratio` 是简化版 Sloan 应计项, 完整版需要资产负债表变动 + 现金流量表

**精确计算方法 (需要从原始报表计算):**
```python
# ROA = 净利润 / 总资产
roa = income.n_income_attr_p / balancesheet.total_assets

# ROIC = EBIT / (总资产 - 无息流动负债)
roic = income.ebit / (balancesheet.total_assets - balancesheet.accounts_pay - ...)

# 毛利率 = (营收 - 营业成本) / 营收
grossprofit_margin = (income.revenue - income.total_cogs) / income.revenue

# 流动比率 = 流动资产 / 流动负债
current_ratio = balancesheet.total_cur_assets / balancesheet.total_cur_liab

# 速动比率 = (流动资产 - 存货) / 流动负债
quick_ratio = (balancesheet.total_cur_assets - balancesheet.inventories) / balancesheet.total_cur_liab

# Sloan 应计项 = (Δ流动资产 - Δ现金 - Δ流动负债 + Δ短期借款 - Δ折旧摊销) / 总资产
accruals = (Δca - Δcash - Δcl + Δstd - Δdep) / total_assets
```

---

## 6. 数据更新方法

### 6.1 无风险利率更新

```bash
# 通过 tushare 下载最新 shibor 数据后重新生成
cd /home/project/hope/Lean
python3 -c "
import pandas as pd
from pathlib import Path
shibor_dir = Path('/home/project/tushare-downloader/tushare_data/shibor')
frames = []
for year_dir in sorted(shibor_dir.iterdir()):
    files = list(year_dir.glob('*.parquet'))
    if files:
        df = pd.read_parquet(files[0])
        if len(df) > 0 and '1y' in df.columns:
            frames.append(df[['date', '1y']].dropna(subset=['1y']))
combined = pd.concat(frames, ignore_index=True).sort_values('date').drop_duplicates(subset='date')
combined.columns = ['trade_date', 'risk_free_rate_1y']
combined['risk_free_rate_1y'] = combined['risk_free_rate_1y'] / 100.0
combined['risk_free_rate_daily'] = combined['risk_free_rate_1y'] / 252.0
combined.to_csv('local_data/risk_free_rate/shibor_1y_risk_free.csv', index=False)
print('Updated:', len(combined), 'rows')
"
```

### 6.2 行业分类更新

```bash
# 重新生成行业分类 (当 tushare index_member_all 更新后)
python3 Scripts/generate_industry_classification.py
```

### 6.3 仿真数据重新生成

```bash
# 分析师一致预期
python3 Scripts/generate_synthetic_estimates.py

# 国债收益率曲线
python3 Scripts/generate_synthetic_yield_curve.py

# 质量因子
python3 Scripts/generate_computed_quality_factors.py
```

---

## 7. 数据质量等级

| 等级 | 说明 | 数据集 |
|------|------|--------|
| A (真实) | 直接从 tushare 下载的真实数据 | 无风险利率, 行业分类 (3,000 股), SW 行业日行情 |
| B (计算) | 基于 tushare 原始数据精确计算 | 质量因子 (ROA, 应计项等) |
| C (近似) | 基于经验关系近似计算 | 质量因子 (毛利率, 流动比率等) |
| D (仿真) | 基于统计模型生成的仿真数据 | 分析师一致预期, 国债收益率曲线长端 |

**使用建议:**
- A 级数据可直接用于回测和实盘
- B 级数据可用于回测, 实盘前建议验证计算精度
- C 级数据仅用于策略逻辑验证, 实盘前需替换为精确计算
- D 级数据仅用于框架测试, 实盘前必须替换为真实数据

---

## 8. 待补充数据 (优先级排序)

| 优先级 | 数据 | 来源 | 费用 | 说明 |
|--------|------|------|------|------|
| P0 | 完整申万行业成分 (5,300+ 股) | tushare `index_member_all` | 免费 (需 2000 积分) | 当前仅 3,000 股, 缺少 2,473 股 |
| P0 | SW2021 行业分类 | tushare `index_classify` (src=SW2021) | 免费 | 当前仅有 SW2014 |
| P1 | 国债收益率曲线 (真实) | ChinaBond 或 tushare | 免费注册 / 5000 积分 | 替换仿真数据 |
| P1 | 分析师一致预期 (真实) | Wind / Choice / iFinD | ¥10,000-50,000/年 | 替换仿真数据 |
| P2 | 北向资金日度明细 | tushare `hsgt_top10` | 需 2000 积分 | 外资流向因子 |
| P2 | 融资融券日度数据 | tushare `margin_detail` | 免费 (已有, 需更新) | 杠杆情绪因子 |
| P2 | 限售解禁日历 | tushare `share_float` | 免费 | 供给冲击因子 |
| P3 | 高频因子 (分钟级) | tushare `min_bar` | 需 5000 积分 | 日内动量/反转 |
| P3 | 期权隐含波动率 | tushare `opt_daily` | 需 3000 积分 | 波动率偏斜策略 |
| P3 | ESG 评级 | Wind / 商道融绿 | 付费 | ESG 因子 |

---

## 9. 与 tushare_data 的关系

本目录 (`local_data/`) 的数据是 tushare_data 的补充, 不重复已有数据:

| 数据类型 | tushare_data (已有) | local_data (补充) |
|----------|---------------------|-------------------|
| 日线行情 | `daily/` (11,004 股) | - |
| 日线估值 | `daily_basic/` (PE/PB/市值/换手率) | - |
| 财务报表 | `income/`, `balancesheet/`, `cashflow/` | - |
| 财务指标 | `fina_indicator/` (120+ 字段) | `computed_quality_factors.csv` (补充 ROA/ROIC 等) |
| 行业分类 | `index_member_all/` (3,000 股) | `ashare_sw31_classification.csv` (5,473 股) |
| 行业行情 | `sw_daily/` (289 指数) | `sw_l1_daily_returns.csv` (L1 筛选) |
| 无风险利率 | `shibor/` (原始) | `shibor_1y_risk_free.csv` (日化处理) |
| 分析师预期 | 无 | `synthetic_consensus_estimates.csv` (仿真) |
| 收益率曲线 | 无 | `synthetic_cgb_yield_curve.csv` (仿真) |
