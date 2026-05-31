# SoloQuant 回测策略数据来源

## 数据管道总览

```
tushare-downloader → /home/project/tushare-downloader/tushare_data → LEAN Data/ → TushareHistoryProvider
```

所有策略均使用 `TushareHistoryProvider` 作为历史数据提供者，数据根目录为 `/home/project/tushare-downloader/tushare_data`。除特别说明外，数据分辨率均为 Daily。

---

## 策略数据需求明细

### 1. 2002-04304-timing-excess-returns

| 项目 | 说明 |
|------|------|
| 语言 | Python + CSharp |
| 数据字段 | close |
| 数据来源 | TushareHistoryProvider |
| 标的池 | Python: 600519.SSE, 000858.SZSE, 600036.SSE, 000001.SZSE, 601318.SSE；CSharp: 10只SSE + 10只SZSE |
| 指标 | MOM(252)，CSharp版用RollingWindow+SMA |
| 特殊 | 12个月动量（252个交易日），月度调仓 |

### 2. 2603-20319-implementation-risk

| 项目 | 说明 |
|------|------|
| 语言 | Python |
| 数据字段 | close |
| 数据来源 | TushareHistoryProvider |
| 标的池 | 600519.SSE, 000858.SZSE, 600036.SSE, 000001.SZSE, 601318.SSE, 600276.SSE, 002594.SZSE |
| 指标 | SMA(200, Daily) |
| 特殊 | SMA200动量策略（论文BM01），close > SMA200则做多，月度调仓 |

### 3. AShareSectorSmallCapAlgorithm

| 项目 | 说明 |
|------|------|
| 语言 | CSharp |
| 数据字段 | close + **外部CSV因子数据**（sector, total_mv, pb, momentum_20d） |
| 数据来源 | TushareHistoryProvider（行情）+ 外部CSV文件 |
| CSV路径 | `/home/project/hope/Lean/local_data/ashare-sector-smallcap-factors.csv` |
| CSV字段 | trade_date, ts_code, sector, total_mv, pb, momentum_20d |
| 标的池 | 动态——从CSV的ts_code列构建 |
| 指标 | 无（使用CSV中预计算的momentum_20d） |
| 特殊 | 唯一使用外部CSV的策略。先选动量最高的行业，再在行业内选小市值+低PB股票，内置蒙特卡洛模拟 |

### 4. momentum-investing-strategy-backtested-over-150-years

| 项目 | 说明 |
|------|------|
| 语言 | Python |
| 数据字段 | close |
| 数据来源 | TushareHistoryProvider |
| 标的池 | 600519.SSE, 000858.SZSE, 600036.SSE, 000001.SZSE, 601318.SSE, 000002.SZSE, 600276.SSE, 300750.SZSE |
| 指标 | 无（直接用History收盘价计算） |
| 特殊 | 经典12-1动量策略（跳过最近1个月），前1/5做多，月度调仓 |

### 5. pdf-online-quantitative-trading-strategies-nyu-stern

| 项目 | 说明 |
|------|------|
| 语言 | Python |
| 数据字段 | close |
| 数据来源 | TushareHistoryProvider |
| 标的池 | 参数化，默认: 600519.SZSE, 000001.SZSE, 600036.SZSE, 000002.SZSE, 600000.SZSE |
| 指标 | 无（PAMR算法代数计算权重） |
| 特殊 | 被动攻击均值回归(PAMR)在线组合选择，日度调仓，价格相对向量驱动梯度下降投影到单纯形 |

### 6. reinforcement-learning-for-portfolio-optimization

| 项目 | 说明 |
|------|------|
| 语言 | Python |
| 数据字段 | close |
| 数据来源 | TushareHistoryProvider |
| 标的池 | 510300.SSE（沪深300ETF）, 511010.SSE（10年国债ETF）, 518880.SSE（黄金ETF） |
| 指标 | RollingWindow(20)对数收益率 |
| 特殊 | 唯一使用A股ETF而非个股的策略。交叉熵法RL，线性softmax策略，日度调仓 |

### 7. risk-parity-asset-allocation-quantpedia

| 项目 | 说明 |
|------|------|
| 语言 | Python |
| 数据字段 | close |
| 数据来源 | TushareHistoryProvider（配置的），但标的为美股 |
| 标的池 | SPY, EFA, GLD, IEF（Market.USA） |
| 指标 | 无（直接用History收盘价算标准差） |
| 特殊 | **唯一使用美股标的的策略**。朴素风险平价：权重=1/波动率（归一化），周度调仓，126天回看。因标的为Market.USA，TushareHistoryProvider可能无法提供数据 |

### 8. short-interest-effect-long-short-version-quantpedia

| 项目 | 说明 |
|------|------|
| 语言 | Python |
| 数据字段 | close（用20日波动率代理卖空利益） |
| 数据来源 | TushareHistoryProvider |
| 标的池 | 600519.SSE, 600036.SSE, 601318.SSE, 600276.SSE, 600887.SSE, 000858.SZSE, 000001.SZSE, 000333.SZSE, 002594.SZSE, 300750.SZSE |
| 指标 | 无（计算20日收益率标准差作为卖空利益代理） |
| 特殊 | 多空策略。低波动率（=低卖空利益代理）→做多，高波动率→做空。底1/4做多，顶1/4做空，月度调仓。**真实卖空数据不可用，用波动率代理** |

### 9. size-factor-hidden-premium-in-small-cap-stocks

| 项目 | 说明 |
|------|------|
| 语言 | Python |
| 数据字段 | close, volume + CoarseFundamental（Price, Volume, DollarVolume） |
| 数据来源 | TushareHistoryProvider + LEAN CoarseFundamental |
| 标的池 | 动态——AddUniverse(CoarseSelectionFunction)，筛选Price>5且Volume>100000，取DollarVolume前200 |
| 指标 | 无 |
| 特殊 | 用`close * volume`代理市值（因FineFundamental受限）。底部10%为小盘股（至少5只），月度调仓 |

### 10. statistical-arbitrage-with-pairs-trading

| 项目 | 说明 |
|------|------|
| 语言 | Python |
| 数据字段 | close / Price |
| 数据来源 | TushareHistoryProvider |
| 标的池 | 601398.SSE（工商银行）, 601939.SSE（建设银行）——固定配对 |
| 指标 | RollingWindow价格比(30天)，手动计算z-score |
| 特殊 | 唯一配对交易策略。价格比z-score：|z|>1.0开仓，|z|<0.5平仓。每腿50%资金，5%追踪止损 |

### 11. the-low-volatility-premium

| 项目 | 说明 |
|------|------|
| 语言 | Python |
| 数据字段 | close |
| 数据来源 | TushareHistoryProvider |
| 标的池 | 参数化，默认: 600519.SSE, 000858.SZSE, 600036.SSE, 000001.SZSE, 601318.SSE |
| 指标 | 无（numpy计算协方差矩阵、逆矩阵、最小方差权重） |
| 特殊 | 最小方差组合优化：w = (Σ⁻¹·1) / (1ᵀ·Σ⁻¹·1)，仅多头约束，月度调仓 |

### 12. 2409-06289-automate-strategy-finding-with-llm

| 项目 | 说明 |
|------|------|
| 语言 | Python（.py.broken）/ CSharp（.cs已删除） |
| 数据字段 | close, volume + RSI, SMA, STD, BollingerBands, MAX |
| 数据来源 | TushareHistoryProvider |
| 标的池 | 上证50样本: 600519.SS, 601318.SS, 600036.SS, 601166.SS, 600030.SS, 601328.SS, 601398.SS, 601939.SS, 601988.SS, 601288.SS |
| 指标 | RSI(14), SMA(14), SMA(20), STD(10), STD(50), BollingerBands(20,2), MAX(20, Field.High) |
| 特殊 | 10个alpha因子+固定权重（论文Table 3），复合得分加权求和，底20%做多+顶20%做空。**文件为.py.broken，策略不可运行** |

### 13. quantitative-finance-arxiv-org

| 项目 | 说明 |
|------|------|
| 语言 | CSharp（源文件不在磁盘，从编译DLL加载） |
| 数据字段 | close + CoarseFundamental.DollarVolume + FineFundamental.EarningRatios.BasicEPS |
| 数据来源 | TushareHistoryProvider + LEAN CoarseFundamental + LEAN FineFundamental |
| 标的池 | 中证500（动态——Coarse+Fine两级筛选） |
| 指标 | 动量信号 |
| 特殊 | **唯一使用FineFundamental数据的策略**（筛选BasicEPS>0）。完整LEAN框架架构（Alpha/Portfolio/Risk模型层）。源码.cs文件不在磁盘，从DLL运行 |

---

## 汇总

### 按数据类型分类

| 数据类型 | 使用策略 |
|----------|----------|
| 仅close | #1, #2, #4, #5, #6, #7, #8, #10, #11 |
| close + volume | #9, #12 |
| close + CoarseFundamental | #9, #13 |
| close + FineFundamental | #13 |
| close + 外部CSV因子 | #3 |
| close + 多指标 | #12 |

### 按市场分类

| 市场 | 策略 |
|------|------|
| A股个股 | #1, #2, #3, #4, #5, #8, #9, #10, #12, #13 |
| A股ETF | #6 |
| 美股ETF | #7 |
| 混合/参数化 | #11 |

### 按标的池分类

| 标的池类型 | 策略 |
|------------|------|
| 固定标的 | #1, #2, #4, #5, #6, #7, #8, #10, #11 |
| 动态（CoarseFundamental筛选） | #9, #13 |
| 动态（CSV文件） | #3 |
| 参数化 | #5, #11 |

### 数据可用性风险

| 风险 | 策略 | 说明 |
|------|------|------|
| 美股数据缺失 | #7 | TushareHistoryProvider为A股专用，SPY/EFA/GLD/IEF可能无数据 |
| FineFundamental不完整 | #13 | LEAN的FineFundamental在A股依赖tushare映射，BasicEPS可能缺失 |
| 外部CSV需维护 | #3 | ashare-sector-smallcap-factors.csv需定期更新 |
| 卖空数据不可用 | #8 | 用波动率代理，非真实卖空利益 |
| .broken文件 | #12 | 策略代码损坏，无法运行 |
