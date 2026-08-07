# 中小盘股有效因子分析 — 基于 tushare_data 本地数据

根据 every300.md 分析结论：涨幅 Top300 中沪深 300 平均仅占 10%，中小盘股是超额收益的主要来源。当前 Barra CNE5 V4 回测仅面向沪深 300，需要扩展因子体系以捕获中小盘超额收益。

---

## 一、当前因子体系回顾

### V4 已有因子（Barra CNE5 基础 + 扩展）

| 因子 | 权重 | 数据来源 | 覆盖范围 |
|------|------|---------|---------|
| momentum（动量） | 0.25 | daily 收盘价 | 全市场 |
| earnyld（盈利收益率） | 0.20 | fina_indicator | 全市场 |
| quality（质量） | 0.20 | fina_indicator | 全市场 |
| growth（成长性） | 0.15 | fina_indicator | 全市场 |
| btop（价值） | 0.10 | daily_basic PB | 全市场 |
| resvol（残差波动率） | -0.10 | daily 收益率 | 全市场 |
| moneyflow（资金流） | 0.10 | moneyflow | 全市场 |
| chipcost（筹码成本） | 0.07 | cyq_perf | 5,477 只 |
| northbound（北向资金） | 0.08 | ccass_hold_detail | 1,827 只 |
| margin（融资融券） | 0.05 | margin_detail | 10,999 只 |
| liquidity（流动性） | 0.05 | daily_basic 换手率 | 全市场 |
| leverage（杠杆） | -0.05 | fina_indicator | 全市场 |
| beta | -0.05 | daily 收益率 | 全市场 |
| size（市值） | -0.05 | daily_basic total_mv | 全市场 |

### 当前因子数据 CSV 列

```
trade_date, beta, momentum, size, earnyld, resvol, growth, btop, leverage, liquidity, nlsize, total_mv, turnover_rate, listed_days, missing_factor_count, is_st
```

覆盖：SSE 2,299 + SZSE 2,882 = **5,181 只**，但 V4 回测配置仅用 `barra-cne5v2-factors_csi300_0608`（300 只沪深 300）。

---

## 二、中小盘波动的深层逻辑：主力四步运作与不可隐藏的痕迹

### 2.1 为什么中小盘的波动不是"随机"的

every300.md 的数据已经证明：涨幅 Top300 中 70% 来自中小盘，且"不在任何指数"的比例在结构性行情中高达 65%。这不是偶然——中小盘的股价运动有明确的**主导力量**。

大盘股（沪深 300）的定价由数千家机构博弈决定，任何单一力量都难以主导方向。但中小盘（尤其市值 < 200 亿）的流通盘小、机构覆盖少，**少数资金即可主导价格走向**。这就是 A 股中小盘波动的底层逻辑：**主力资金运作**（传统称"坐庄"）。

价格只是运作的结果。要提前识别，必须追踪运作过程本身。

---

### 2.2 中小盘 Alpha 的四大理论驱动力

主力四步运作（吸筹→洗盘→拉升→出货）是中小盘 Alpha 的**运作机制**，但机制背后有更底层的**理论驱动力**。理解这些驱动力，才能判断哪些因子在什么条件下有效，以及何时会失效。

#### 驱动力一：流动性摩擦与规模溢价

市值因子（Size Factor）在 A 股被视为最强健的 Alpha 来源之一，但其背后隐藏的**真实驱动力是流动性溢价（Liquidity Premium）**。与成熟市场不同，A 股散户占比较高，中小盘股面临更高的交易摩擦和信息不对称。

根据 Amihud 模型，流动性风险 = 资产价格对交易量的敏感度。在 A 股，低流动性在中小盘中**不是单纯的风险，而是超额收益的来源**：
- 流动性充裕的扩张周期，中小盘因流通盘小，更容易被资金推升，表现出极高的 Beta 敏感性和 Alpha 爆发力
- 流动性紧缩周期，中小盘因退出困难而加速下跌，Alpha 迅速变为负值

**量化启示**：简单剔除流动性指标会误杀因"流动性折价"而具备高弹性的标的。正确做法是将流动性分解为**水平**（换手率、Amihud）和**变化**（流动性改善/恶化趋势），后者才是 Alpha 信号。

**对四步运作的解释**：吸筹阶段主力必须承受低流动性成本（买入冲击），这种成本是后三个阶段超额收益的来源——流通盘被锁定后，少量买盘即可大幅推升股价。

---

#### 驱动力二：异质波动率与"彩票效应"

学术研究中，异质波动率（Idiosyncratic Volatility, IVOL）往往与低收益挂钩，但在 A 股中小盘中**这一逻辑显著反转**。

A 股散户倾向于追逐高波动资产——"彩票偏好"（Lottery-like Preference）。中小盘股因散户化特征强，高 IVOL 股票短期内因情绪溢价产生显著 Alpha，但长期面临均值回归的风险。

**关键区分**：必须将波动率分解为两部分：
- **系统性波动率**（由宏观因子驱动）→ 承担此风险是被动暴露，不产生 Alpha
- **异质波动率**（由个体情绪与预期差异驱动，且与特定基本面事件耦合）→ 捕获此波动才是 Alpha

**量化启示**：传统的 resvol 因子（Barra 残差波动率）将所有异质波动一视同仁地看空（-0.10），这在中小盘中过于粗暴。正确做法是：
- **高 IVOL + 无基本面催化** → 纯粹的散户博傻，看空（-）
- **高 IVOL + 有基本面事件（业绩惊喜、政策催化）** → 情绪与价值共振，看多（+）

**对四步运作的解释**：拉升阶段的高波动率是主力运作的结果，也是散户跟风的催化剂。但出货阶段的高波动率（高位震荡）是筹码分散的标志，方向完全不同。同一水平的波动率在不同阶段含义截然相反。

---

#### 驱动力三：动量效应的非线性演进

与大盘蓝筹的长周期动量趋势不同，中小盘的动量表现为**"短周期、高频次"的强延续性**。原因有二：
1. A 股信息发酵速度极快，中小盘的信息不对称更严重
2. 资金在中小盘板块间的轮动具有显著的**羊群效应（Herding Effect）**——当细分赛道受政策/行业催化时，资金快速涌入形成正反馈循环

**量化启示**：传统 12 月动量因子在中小盘中效果差，因为中小盘的动量生命周期更短：
- **1 周~1 月**：动量延续性最强（拉升阶段）
- **1~3 月**：动量仍有效但衰减（拉升后期/出货初期）
- **3~12 月**：动量反转概率增大（出货完成后的均值回归）

引入**基于量价关系的动量指标**（如放量上涨=真动量 vs 缩量上涨=假动量），比纯收益率动量更有效。

**对四步运作的解释**：吸筹阶段没有动量（横盘），洗盘阶段短期动量为负但即将反转，拉升阶段短期动量极强，出货阶段短期动量仍在但量价开始背离。动量的方向和强度本身是阶段识别的核心信号。

---

#### 驱动力四：成长预期与结构化估值重塑

中小盘 Alpha 与成长因子（Growth）及质量因子（Quality）存在动态博弈。与大盘股注重当期 ROE 和现金流不同，中小盘的估值逻辑更多基于**未来盈利的贴现预期**：

- "中小盘 Alpha"很大程度上是**"小盘成长股"溢价**——当 size × growth 交互分析时，真正产生 Alpha 的是小盘 + 高增长组合
- 这些公司在特定政策窗口期（如硬科技支持政策），通过**研发投入带来的估值修复**构成 Alpha 的重要来源
- 质量因子（ROE、净利率）在中小盘中的适用性低于成长因子，因为高 ROE 的中小盘往往已经"长大"（市值 > 200 亿），不再属于小盘范畴

**量化启示**：
- size 和 growth 不应独立建模，应引入 **size × growth 交互因子**
- R&D 投入占比（`income → rd_exp / revenue`）是中小盘成长股的领先指标
- 质量因子的阈值应动态调整：中小盘中 ROE > 8% 即为"高质量"（大盘股标准是 > 15%）

**对四步运作的解释**：主力选择标的时，成长预期是关键考量——有业绩故事（国产替代、新赛道、政策红利）的中小盘更容易吸引跟风盘，出货时故事更好讲。纯炒概念的无成长小盘，拉升后极易 A 字杀，因为缺乏"估值重塑"的叙事支撑。

---

### 2.3 四大驱动力与四步运作的关系

| 驱动力 | 对吸筹的影响 | 对洗盘的影响 | 对拉升的影响 | 对出货的影响 |
|--------|------------|------------|------------|------------|
| 流动性溢价 | 承受低流动性成本建仓 | 流动性下降确认筹码锁定 | 流动性改善触发 Alpha 爆发 | 流动性高峰但方向逆转 |
| 异质波动率 | 低 IVOL = 横盘吸筹 | IVOL 短暂上升（恐慌） | IVOL 暴增（散户跟风） | IVOL 高位但方向从涨→跌 |
| 动量非线性 | 无动量（横盘） | 短期动量负值 | 1M 动量极强 | 1M 动量强但量价背离 |
| 成长预期 | 选择有成长故事的标的 | 基本面不变验证洗盘 | 业绩释放配合拉升 | 利好消息配合出货 |

**核心结论**：四大驱动力解释了**为什么**中小盘存在可预测的超额收益，四步运作解释了**如何**捕捉这些收益。因子体系必须同时覆盖两个维度：驱动力维度（流动性、IVOL、动量、成长）和阶段维度（吸筹、洗盘、拉升、出货）。

---

### 2.4 主力四步运作模型

主力运作一只股票，必然经历四个阶段。这不是理论假设，而是由资金运作的物理规律决定的——你不可能不买入就持有，不可能不卖出就获利了结。

```
吸筹（建仓）→ 洗盘（震仓）→ 拉升（主升浪）→ 出货（派发）
   ↓              ↓              ↓              ↓
 低价买入      清洗浮筹       推高股价       高价卖出
 持续数月      1-4 周         1-3 月         持续数周~数月
```

#### 第一阶段：吸筹（Accumulation）

**核心动作**：在低价区间持续买入，目标是收集足够筹码（通常 10%~30% 流通盘）

**价格特征**：
- 股价长期横盘或缓慢下跌后企稳
- 底部逐渐抬高（低点一个比一个高）
- 涨时放量、跌时缩量（买入积极，卖出消极）

**主力无法隐藏的痕迹**：

| 痕迹 | 为什么无法隐藏 | 数据来源 |
|------|--------------|---------|
| **成交量在底部区间异常放大** | 买入必须有人卖出，交易量是物理记录 | daily → vol, amount |
| **大单/超大单净买入持续为正** | 主力建仓单笔金额大，即使拆单，日汇总仍为净流入 | moneyflow → net_mf_amount |
| **股东户数持续减少** | 筹码从散户（多户）→主力（少户），户数下降是结构性变化 | stk_holdernumber → holder_num |
| **筹码成本向低价区间集中** | 大量成交发生在底部，cyq_perf 的 cost_50pct 下移 | cyq_perf → cost_5pct~95pct |
| **融资余额缓慢增加** | 主力可能使用融资杠杆建仓 | margin_detail → rzye |
| **十大流通股东出现新面孔** | 季报披露时，新机构/信托/资管计划出现在前十大 | top10_floatholders |

**散户可识别的量化信号**：
- 20 日成交量均值 > 前 60 日成交量均值的 1.5 倍，但股价涨幅 < 10%（放量不涨 = 暗中吸筹）
- 大单净买入金额 20 日累计 > 0，且小单净卖出（散户被洗出）
- 股东户数环比下降 > 10%

---

#### 第二阶段：洗盘（Shakeout）

**核心动作**：通过打压股价制造恐慌，迫使意志不坚定的散户卖出，进一步集中筹码

**价格特征**：
- 突然急跌（-5%~-15%），但很快收回
- 跌破前期支撑位制造破位假象
- 成交量在下跌时反而缩小（主力不卖，只是不托盘）

**主力无法隐藏的痕迹**：

| 痕迹 | 为什么无法隐藏 | 数据来源 |
|------|--------------|---------|
| **缩量下跌** | 主力不卖，卖压仅来自散户恐慌，量能必然萎缩 | daily → vol |
| **大单净卖出为负但金额极小** | 主力不主动卖出，大单流出远小于拉升时流入 | moneyflow → buy_lg_amount, sell_lg_amount |
| **跌停板封不住** | 主力在跌停价位承接，跌停板反复打开 | stk_limit → up_limit, down_limit + daily → close |
| **筹码成本不随股价下移** | 股价跌了但 cost_50pct 不跌，说明底部筹码锁定 | cyq_perf → cost_50pct vs close |
| **融资余额不降反增** | 主力利用下跌加杠杆买入 | margin_detail → rzye |

**散户可识别的量化信号**：
- 股价跌破 20 日均线，但成交量 < 20 日均量的 0.6 倍（缩量假破位）
- 5 日内跌幅 > 8%，但大单净流出 < 总成交额的 2%（主力未出逃）
- 筹码获利盘比例（winner_rate）短暂下降后快速回升

---

#### 第三阶段：拉升（Markup）

**核心动作**：快速推高股价，脱离成本区，吸引跟风盘

**价格特征**：
- 连续阳线或大阳线，涨幅加速
- 成交量急剧放大
- 常伴随涨停板
- 回调幅度小、时间短（"只调不跌"）

**主力无法隐藏的痕迹**：

| 痕迹 | 为什么无法隐藏 | 数据来源 |
|------|--------------|---------|
| **成交量爆发式增长** | 拉升需要真金白银买入，量能无法伪造 | daily → vol, amount |
| **大单净买入激增** | 拉升阶段主力必须主动进攻，大单流入暴增 | moneyflow → buy_elg_amount, buy_lg_amount |
| **涨停板频繁出现** | 中小盘拉升必然触及涨停，这是物理限制 | stk_limit + daily → close |
| **换手率飙升** | 主力对倒+散户跟风，换手率必然上升 | daily_basic → turnover_rate |
| **融资余额快速增加** | 散户跟风融资买入 | margin_detail → rzye |
| **龙虎榜出现** | 涨幅异常必然上龙虎榜，机构/游资席位暴露 | top_inst → exalter, buy, sell |

**散户可识别的量化信号**：
- 5 日涨幅 > 20%，且成交量 > 前 20 日均量的 3 倍
- 连续 3 日大单净买入占比 > 15%
- 涨停后次日高开 > 3%（强势确认）
- 换手率从 < 2% 突增至 > 8%

---

#### 第四阶段：出货（Distribution）

**核心动作**：在高位将筹码派发给跟风散户，完成获利了结

**价格特征**：
- 高位震荡，涨跌交替（"出货K线"：长上影线、高开低走）
- 成交量维持高位但股价不再创新高（量价背离）
- 利好消息频出（配合出货的"故事"）

**主力无法隐藏的痕迹**：

| 痕迹 | 为什么无法隐藏 | 数据来源 |
|------|--------------|---------|
| **大单净卖出转正** | 主力出货必须卖出，大单方向必然逆转 | moneyflow → sell_lg_amount, sell_elg_amount |
| **量价背离** | 成交量高但价格不涨，说明有人在大量卖出 | daily → vol, close |
| **股东户数激增** | 筹码从主力（少户）→散户（多户），户数暴增 | stk_holdernumber → holder_num |
| **筹码成本向高位扩散** | 大量成交发生在高位，cost_85pct 上移 | cyq_perf → cost_85pct, cost_95pct |
| **融资余额见顶回落** | 散户融资买入到极限后开始偿还 | margin_detail → rzye, rzche |
| **十大股东减持** | 季报显示前十大股东持股比例下降 | top10_floatholders |
| **高管/关联方减持** | 内部人先知先觉，减持是最明确的信号 | stk_holdertrade → in_de, change_vol |
| **北向资金流出** | 聪明钱先走 | ccass_hold_detail → col_shareholding |

**散户可识别的量化信号**：
- 股价创新高但成交量 < 前高时成交量（顶背离）
- 大单净卖出连续 3 日为正，且金额 > 总成交额 10%
- 股东户数环比增加 > 15%
- 高位出现长上影线（收盘价远低于最高价）

---

### 2.5 四阶段不可隐藏痕迹汇总

**核心原理**：交易是零和博弈，每一笔买入必有对应卖出，所有交易记录永久保存在交易所。主力可以拆单、可以对倒、可以制造假象，但以下结构性痕迹**物理上不可能隐藏**：

| 不可隐藏痕迹 | 物理原因 | 对应阶段 | tushare_data 支持 |
|-------------|---------|---------|------------------|
| **成交量变化** | 交易必须记录，无法抹除 | 全阶段 | daily → vol, amount ✅ |
| **大单资金流向** | 拆单可降低单笔，但日汇总方向不变 | 吸筹/出货 | moneyflow ✅ |
| **股东户数变化** | 股权过户必须登记，户数是精确统计 | 吸筹↓/出货↑ | stk_holdernumber ✅（季度） |
| **筹码成本分布** | 成交价和量决定成本分布，数学上确定 | 吸筹集中/出货分散 | cyq_perf ✅（50%覆盖） |
| **涨跌停记录** | 交易所公开数据 | 拉升 | stk_limit ✅ |
| **融资融券余额** | 券商必须每日上报 | 吸筹增/出货减 | margin_detail ✅ |
| **十大股东变动** | 季报法定披露 | 吸筹新进/出货退出 | top10_floatholders ✅（季度） |
| **龙虎榜** | 异常波动自动触发披露 | 拉升 | top_inst ✅ |
| **高管增减持** | 法定披露，内幕交易必留痕 | 吸筹前买入/出货前卖出 | stk_holdertrade ⚠️（数据稀疏） |
| **大宗交易** | 大额转让必须通过大宗通道 | 吸筹/出货 | block_trade ⚠️（数据稀疏） |

---

### 2.6 tushare_data 无法覆盖的关键数据

以下数据对识别主力运作至关重要，但 tushare_data **不包含**：

| 数据 | 作用 | 为什么重要 | 获取渠道 |
|------|------|-----------|---------|
| **Level 2 逐笔委托/成交** | 识别拆单、挂单撤单行为 | 主力拆单在日线级别不可见，逐笔级别暴露 | Wind/iFinD/东方财富Level2 |
| **委托队列（买卖五档快照）** | 识别挂大单压盘/托盘 | 主力常在卖一挂大单压价吸筹，买一挂大单托盘出货 | 东方财富Level2 实时 |
| **大单追踪（单笔 > 50 万）** | 精确追踪主力单笔操作 | moneyflow 是日汇总，大单追踪可看单笔方向 | 同花顺大单追踪 |
| **机构席位明细** | 龙虎榜机构席位具体买卖金额 | top_inst 有数据但按日分区稀疏 | tushare `top_inst` API（需重新下载） |
| **融资融券明细（券商维度）** | 哪些券商在加杠杆 | 券商营业部维度可追踪主力资金来源 | Wind 融资融券明细 |
| **港股通持股明细（按参与者）** | ccass 参与者持仓变化 | 可追踪外资机构具体操作 | ccass_hold_detail ✅（已有） |
| **限售股解禁日历** | 解禁前后主力行为变化 | 解禁是出货窗口，主力常配合解禁拉高 | tushare `share_float_d` API |
| **舆情/社交媒体热度** | 出货阶段配合利好消息 | 主力出货时常释放利好吸引散户 | 东方财富股吧/雪球/同花顺论股 |
| **分析师评级调整** | 配合出货的"买入"评级 | 券商研报是出货帮凶 | Wind/iFinD 分析师评级 |

---

### 2.7 用现有数据构建"主力运作阶段识别"因子

即使没有 Level 2 数据，仅用 tushare_data 的日线数据，也可以构建有效的阶段识别因子：

#### 因子 1：吸筹识别因子（Accumulation Score）

```python
# 5 个子信号加权
acc_vol = (vol.rolling(20).mean() > vol.rolling(60).mean() * 1.5) & (pct_chg.rolling(20).sum() < 10)  # 放量不涨
acc_mf = moneyflow_net_20d > 0  # 大单持续净流入
acc_holder = holder_change_qoq < -0.10  # 股东户数减少 > 10%
acc_chip = (cost_50pct < close * 0.95) & (winner_rate < 0.3)  # 筹码在低位集中
acc_margin = rzye_change_5d > 0  # 融资余额增加

accumulation_score = 0.3*acc_vol + 0.25*acc_mf + 0.2*acc_holder + 0.15*acc_chip + 0.1*acc_margin
```

**数据来源**：daily + moneyflow + stk_holdernumber + cyq_perf + margin_detail
**信号方向**：分数越高，吸筹概率越大 → **买入信号**

#### 因子 2：洗盘识别因子（Shakeout Score）

```python
shake_price = (close < ma20) & (close > ma60)  # 跌破 20 日线但在 60 日线上
shake_vol = vol < vol.rolling(20).mean() * 0.6  # 缩量
shake_mf = abs(moneyflow_net_5d) < total_amount_5d * 0.02  # 大单几乎无流出
shake_chip = cost_50pct_change_5d < close_change_5d  # 筹码成本不随股价下移

shakeout_score = 0.35*shake_price + 0.3*shake_vol + 0.2*shake_mf + 0.15*shake_chip
```

**数据来源**：daily + moneyflow + cyq_perf
**信号方向**：洗盘结束 → **加仓信号**

#### 因子 3：出货识别因子（Distribution Score）

```python
dist_diverge = (close > close.rolling(20).max()) & (vol < vol.rolling(20).max() * 0.7)  # 量价背离
dist_mf = moneyflow_net_5d < 0  # 大单净流出
dist_holder = holder_change_qoq > 0.15  # 股东户数激增
dist_chip = (cost_95pct > close * 1.05) & (winner_rate > 0.8)  # 筹码高位分散
dist_margin = rzye_change_5d < 0  # 融资余额下降

distribution_score = 0.3*dist_diverge + 0.25*dist_mf + 0.2*dist_holder + 0.15*dist_chip + 0.1*dist_margin
```

**数据来源**：daily + moneyflow + stk_holdernumber + cyq_perf + margin_detail
**信号方向**：分数越高，出货概率越大 → **卖出信号**

#### 因子 4：拉升强度因子（Markup Strength）

```python
markup_ret = pct_chg.rolling(5).sum() > 20  # 5 日涨幅 > 20%
markup_vol = vol > vol.rolling(20).mean() * 3  # 成交量 3 倍以上
markup_mf = moneyflow_net_5d / total_amount_5d > 0.15  # 大单净买入占比 > 15%
markup_limit = limit_up_count_20d >= 2  # 20 日内 2 次以上涨停

markup_strength = 0.3*markup_ret + 0.25*markup_vol + 0.25*markup_mf + 0.2*markup_limit
```

**数据来源**：daily + moneyflow + stk_limit
**信号方向**：强度越高，拉升越确定 → **持有/追涨信号**（但需配合出货因子判断顶部）

---

### 2.8 四阶段因子与现有 Barra 因子的关系

| 主力运作阶段 | 最有效的 Barra 因子 | 最有效的阶段识别因子 | 互补关系 |
|-------------|-------------------|-------------------|---------|
| 吸筹 | size（负）, btop（正） | accumulation_score | Barra 选出低估值小盘，阶段因子确认主力在建仓 |
| 洗盘 | resvol（负）, reversal（正） | shakeout_score | Barra 选出低波动/超跌股，阶段因子确认是洗盘而非下跌 |
| 拉升 | momentum（正）, moneyflow（正） | markup_strength | Barra 选出强势股，阶段因子确认拉升还在继续 |
| 出货 | momentum（正但危险） | distribution_score | **Barra 无法识别顶部**，阶段因子是唯一的卖出信号 |

**关键洞察**：Barra 因子本质上是"截面因子"——在同一时间点比较所有股票，选出相对好的。但**主力运作是时间序列现象**——同一只股票在不同阶段，因子方向完全不同。纯 Barra 因子会在出货阶段仍然给出"动量强→买入"的错误信号，而阶段识别因子可以发出卖出警告。

---

## 三、中小盘股的有效因子

学术研究和 A 股实证表明，以下因子在中小盘中比大盘股更有效。这些因子本质上是对**主力四步运作**不同阶段的量化捕捉（详见 2.4 节），同时也对应**四大理论驱动力**（详见 2.2 节）：

### 第一梯队：强有效（IC > 0.05，长期显著）— 直接追踪主力行为

| 因子 | 方向 | 有效性解释 | 对应驱动力 | 适用市值 |
|------|------|-----------|-----------|---------|
| **短期反转** | 负（跌多了反弹） | 洗盘结束后的反弹，本质是主力洗盘后恢复拉升 | 动量非线性 | < 200 亿 |
| **动量生命周期** | 非线性 | 识别拉升阶段（1M/3M强）vs 出货阶段（12M衰减） | 动量非线性 | < 500 亿 |
| **筹码集中度** | 正 | 直接度量吸筹程度，筹码集中=主力持仓比例高 | 流动性溢价 | < 300 亿 |
| **资金流净流入** | 正 | 直接追踪主力资金方向，吸筹/拉升时净流入 | 流动性溢价 | < 200 亿 |
| **成交量异动** | 正 | 吸筹放量不涨 + 拉升放量上涨，不同模式对应不同阶段 | 流动性溢价+动量 | < 200 亿 |
| **业绩惊喜** | 正 | 主力常配合业绩释放拉升，"惊喜"是出货前奏或吸筹催化剂 | 成长预期+IVOL | < 500 亿 |
| **size×growth 交互** | 正 | 小盘+高成长才是真正的 Alpha 源，纯小盘无成长是噪音 | 成长预期 | 30~200 亿 |

### 第二梯队：中有效（IC 0.03~0.05，特定环境有效）

| 因子 | 方向 | 有效性解释 | 对应驱动力 | 适用市值 |
|------|------|-----------|-----------|---------|
| **条件性 IVOL** | 条件性 | 高 IVOL + 有基本面催化 → 正 Alpha；高 IVOL 无催化 → 负 Alpha | 异质波动率 | < 300 亿 |
| **波动率反转** | 负（低波动→高收益） | 吸筹期主力压价导致波动率低 | 异质波动率 | < 200 亿 |
| **换手率** | 负（低换手→高收益） | 低换手=筹码锁定=主力持仓，高换手=出货或散户接力 | 流动性溢价 | < 300 亿 |
| **股东户数变化** | 负（户数减少→筹码集中→涨） | 最直接的吸筹/出货指标 | 流动性溢价 | < 200 亿 |
| **融资融券余额变化** | 正 | 融资增加=主力加杠杆吸筹或散户跟风拉升 | 流动性溢价 | < 300 亿 |
| **涨跌停因子** | 正（涨停次数多→短期强势） | 拉升阶段涨停是主力的进攻手段 | 动量非线性 | < 100 亿 |
| **北向资金流入** | 正 | 聪明钱先于散户介入 | 流动性溢价 | 沪深股通标的 |
| **R&D 投入占比** | 正 | 研发投入是中小盘成长股估值修复的领先指标 | 成长预期 | < 300 亿 |
| **流动性改善趋势** | 正 | 换手率从低→高的拐点，是拉升启动前兆 | 流动性溢价 | < 200 亿 |

### 第三梯队：弱有效但可增强（IC 0.01~0.03）

| 因子 | 方向 | 说明 | 对应驱动力 |
|------|------|------|-----------|
| **市值因子** | 负（小市值→高收益） | A 股小市值溢价长期存在但 2017 年后减弱，需与 growth 交互 | 流动性溢价 |
| **非流动性（Amihud）** | 负（低流动性→高收益） | 小盘股流动性溢价，但需区分水平 vs 趋势 | 流动性溢价 |
| **大宗交易溢价** | 正（溢价成交→看多信号） | 大宗交易数据中小盘中信息量大 | 流动性溢价 |

---

## 四、因子计算方法与数据映射

### 4.1 短期反转因子（强推荐）

**计算方法**：过去 5/10/20 个交易日收益率取负值

```python
# 5 日反转
reversal_5d = -1 * pct_chg.rolling(5).sum()
# 10 日反转
reversal_10d = -1 * pct_chg.rolling(10).sum()
```

**数据来源**：`tushare_data/daily` → `pct_chg` 列
**覆盖**：11,046 只
**计算复杂度**：低，纯价格数据
**状态**：可直接计算，数据完备

---

### 4.2 动量生命周期因子（强推荐）

**计算方法**：短期动量（1M）vs 长期动量（12M）的比值，识别动量加速/衰减阶段

```python
mom_1m = close.pct_change(21)       # 1 月动量
mom_3m = close.pct_change(63)       # 3 月动量
mom_12m = close.pct_change(252)     # 12 月动量
# 动量加速度
mom_accel = mom_1m - mom_3m / 3     # 短期动量是否加速
```

**数据来源**：`tushare_data/daily` → `close`, `adj_factor`（需复权）
**覆盖**：11,046 只
**计算复杂度**：低
**状态**：当前 Barra momentum 因子已有，但仅用 12M 动量；需增加 1M/3M 多周期维度

---

### 4.3 筹码集中度因子（强推荐）

**计算方法**：筹码分布的集中度指标

```python
# 方法1：使用 cyq_perf 的成本分布分位数
chip_concentration = (cost_85pct - cost_15pct) / cost_50pct  # 筹码集中度
# 方法2：90% 筹码集中度
chip_90 = (cost_95pct - cost_5pct) / cost_50pct
# 获利盘比例
winner_signal = winner_rate  # 直接使用
```

**数据来源**：`tushare_data/cyq_perf` → `cost_5pct`, `cost_15pct`, `cost_50pct`, `cost_85pct`, `cost_95pct`, `winner_rate`
**覆盖**：5,477 只（约 50% 的 A 股）
**计算复杂度**：低，直接列计算
**状态**：可直接计算。**注意覆盖不完整**，缺失股票需用 fallback

---

### 4.4 资金流因子（强推荐，V4 已有）

**计算方法**：大单/超大单净买入占比

```python
# 大单净买入金额占比
lg_net = (buy_lg_amount - sell_lg_amount) + (buy_elg_amount - sell_elg_amount)
total_amount = buy_sm_amount + sell_sm_amount + buy_md_amount + sell_md_amount + ...
money_flow_ratio = lg_net / total_amount
# 5 日滚动均值
mf_5d = money_flow_ratio.rolling(5).mean()
```

**数据来源**：`tushare_data/moneyflow` → `buy_lg_amount`, `sell_lg_amount`, `buy_elg_amount`, `sell_elg_amount`
**覆盖**：11,022 只
**计算复杂度**：低，但单只 4,558 行 × 20 列，全量加载需 ~3GB 内存
**状态**：V4 已有 moneyflow 权重 0.10，可增强为多周期（1D/5D/20D）

---

### 4.5 成交量异动因子（强推荐）

**计算方法**：成交量相对均值的偏离度

```python
# 成交量比率（当前量 / 20 日均量）
vol_ratio = vol / vol.rolling(20).mean()
# 成交额异动
amount_ratio = amount / amount.rolling(20).mean()
```

**数据来源**：`tushare_data/daily` → `vol`, `amount`
**覆盖**：11,046 只
**计算复杂度**：极低
**注意**：`daily_basic` 也有 `volume_ratio` 列可直接使用
**状态**：数据完备，可直接计算

---

### 4.6 业绩惊喜因子（强推荐）

**计算方法**：实际业绩 vs 预告业绩的差异

```python
# 方法1：业绩预告上下限中值 vs 实际
forecast_mid = (p_change_min + p_change_max) / 2
actual_change = net_profit_yoy  # 从 fina_indicator
surprise = actual_change - forecast_mid

# 方法2：单季度营收/净利同比变化
q_surprise = q_sales_yoy - q_sales_yoy.shift(4)  # 同比 vs 上一期同比
```

**数据来源**：
- `tushare_data/forecast` → `p_change_min`, `p_change_max`, `net_profit_min`, `net_profit_max`（10,962 只）
- `tushare_data/fina_indicator` → `netprofit_yoy`, `q_sales_yoy`, `q_op_qoq`

**覆盖**：forecast 10,962 只，fina_indicator 10,962 只
**计算复杂度**：中，需匹配 ann_date 与 trade_date
**状态**：数据完备，需编写因子计算脚本
**理论对应**：成长预期驱动 + 条件性 IVOL 驱动（惊喜事件是 IVOL 的基本面锚定）

---

### 4.7 size×growth 交互因子（强推荐，新增）

**计算方法**：市值与成长性的交互项，识别"小盘成长股"溢价

```python
# 标准化
size_z = (total_mv - total_mv.mean()) / total_mv.std()  # 小市值 → size_z 负值
growth_z = (q_sales_yoy - q_sales_yoy.mean()) / q_sales_yoy.std()  # 高增长 → growth_z 正值
# 交互因子：小盘 × 高增长 → 极负值（因 size_z 为负）
size_growth = size_z * growth_z  # 取负 = 看多小盘成长股
signal = -size_growth  # 越大越好：小市值 + 高增长
```

**数据来源**：
- `tushare_data/daily_basic` → `total_mv`（市值）
- `tushare_data/fina_indicator` → `q_sales_yoy`, `netprofit_yoy`, `op_yoy`（成长性）

**覆盖**：11,047 只（市值）+ 10,962 只（成长性）
**计算复杂度**：低
**状态**：数据完备，可直接计算
**理论对应**：成长预期驱动——纯 size 因子（-0.15）是粗略的，size×growth 交互才是真正的 Alpha 源

---

### 4.8 条件性 IVOL 因子（中推荐，新增）

**计算方法**：将异质波动率与基本面催化事件结合，形成条件性信号

```python
# 计算 IVOL（残差波动率）
residuals = pct_chg - beta * market_return  # 剔除市场因子后的残差
ivol = residuals.rolling(20).std() * np.sqrt(252)  # 年化异质波动率

# 基本面催化事件（任一为 True）
has_catalyst = (
    (abs(surprise) > 10) |           # 业绩惊喜 > 10%
    (q_sales_yoy > 30) |             # 营收增速 > 30%
    (rd_exp / revenue > 0.08)         # 研发投入占比 > 8%
)

# 条件性 IVOL
cond_ivol = ivol * has_catalyst * 1 - ivol * (~has_catalyst) * (-1)
# 有催化 → 高 IVOL 是正 Alpha（情绪与价值共振）
# 无催化 → 高 IVOL 是负 Alpha（纯散户博傻）
```

**数据来源**：
- `tushare_data/daily` → `pct_chg`（计算 IVOL）
- `tushare_data/fina_indicator` → `q_sales_yoy`, `netprofit_yoy`
- `tushare_data/income` → `rd_exp`, `total_revenue`

**覆盖**：11,046 只 + 10,962 只 + 10,962 只
**计算复杂度**：中，需多表拼接
**状态**：数据完备
**理论对应**：异质波动率驱动——解决了传统 resvol 因子将所有 IVOL 一视同仁看空的缺陷

---

### 4.9 R&D 投入占比因子（中推荐，新增）

**计算方法**：研发费用占营收比例

```python
rd_ratio = rd_exp / total_revenue  # 研发投入占比
# 高 rd_ratio 在中小盘中是估值修复的领先指标
# 特别是硬科技/国产替代赛道，rd_ratio > 10% 是成长性标志
```

**数据来源**：`tushare_data/income` → `rd_exp`, `total_revenue`
**覆盖**：10,962 只
**计算复杂度**：低
**状态**：数据完备
**理论对应**：成长预期驱动——R&D 投入是中小盘"结构化估值重塑"的物质基础

---

### 4.10 流动性改善趋势因子（中推荐，新增）

**计算方法**：换手率的趋势变化，识别从低流动性→高流动性的拐点

```python
# 换手率 5 日均值 vs 20 日均值
turnover_short = turnover_rate.rolling(5).mean()
turnover_long = turnover_rate.rolling(20).mean()
# 流动性改善比率
liq_improve = turnover_short / turnover_long
# 拐点信号：从 < 1 变为 > 1（流动性开始改善）
liq_turning = (liq_improve > 1) & (liq_improve.shift(5) < 1)
```

**数据来源**：`tushare_data/daily_basic` → `turnover_rate`
**覆盖**：11,047 只
**计算复杂度**：低
**状态**：数据完备
**理论对应**：流动性溢价驱动——流动性改善拐点（而非绝对水平）是 Alpha 启动信号

---

### 4.11 股东户数变化因子（中推荐）

**计算方法**：股东户数环比变化率

```python
holder_change = (holder_num - holder_num.shift(1)) / holder_num.shift(1)
# 户数减少 → 筹码集中 → 看多
holder_signal = -holder_change
```

**数据来源**：`tushare_data/stk_holdernumber` → `holder_num`, `ann_date`, `end_date`
**覆盖**：148 个分区（按报告期分区，非按股票），约 5,500 行/文件
**计算复杂度**：低，但数据按季度更新，频率低
**状态**：可直接计算。**注意**：按报告期分区（非按股票），需要全量加载后按 ts_code 筛选

---

### 4.12 融资融券因子（中推荐，V4 已有）

**计算方法**：融资余额变化率

```python
# 融资余额 5 日变化率
rz_change = rzye.pct_change(5)
# 融资买入额占比
rz_ratio = rzmre / (rzmre + rzche)
```

**数据来源**：`tushare_data/margin_detail` → `rzye`（融资余额）, `rzmre`（融资买入额）, `rzche`（融资偿还额）
**覆盖**：10,999 只
**计算复杂度**：低
**状态**：V4 已有 margin 权重 0.05，可增强为多维度

---

### 4.13 涨跌停因子（中推荐）

**计算方法**：统计近期涨停/跌停次数

```python
# 当日收盘价是否触及涨停价
hit_up = (close >= up_limit * 0.995)   # 允许 0.5% 误差
hit_down = (close <= down_limit * 1.005)
# 20 日涨停次数
limit_up_count = hit_up.rolling(20).sum()
```

**数据来源**：`tushare_data/stk_limit` → `up_limit`, `down_limit`, `pre_close`
**覆盖**：13,158 个分区（按日期分区），每分区 ~2,671 行
**计算复杂度**：中，需按日期分区加载后合并
**状态**：数据完备，需编写因子计算脚本。**注意**：按 `trade_date` 分区，需要加载多个分区

---

### 4.14 Amihud 非流动性因子（弱推荐）

**计算方法**：Amihud (2002) 非流动性指标

```python
# 日非流动性 = |日收益率| / 日成交额
illiq_daily = abs(pct_chg / 100) / (amount * 1000)
# 20 日均值
amihud = illiq_daily.rolling(20).mean()
```

**数据来源**：`tushare_data/daily` → `pct_chg`, `amount`
**覆盖**：11,046 只
**计算复杂度**：低
**状态**：可直接计算

---

### 4.15 北向资金因子（中推荐，V4 已有）

**计算方法**：北向持股比例变化

```python
# CCAS 持股集中度
ccas_total = ccass_hold_detail.groupby(['trade_date', 'ts_code'])['col_shareholding'].sum()
# 持股变化率
north_change = ccas_total.pct_change(5)
```

**数据来源**：`tushare_data/ccass_hold_detail` → `col_shareholding`, `col_shareholding_percent`
**覆盖**：1,827 个分区（按日期分区），但仅覆盖港股通标的（约 1,500 只）
**计算复杂度**：中
**状态**：V4 已有 northbound 权重 0.08。**注意**：仅覆盖沪深股通标的，中小盘很多不在范围内

---

### 4.16 大宗交易因子（弱推荐）

**计算方法**：大宗交易溢价率

```python
# 大宗交易成交价 vs 当日收盘价的溢价
block_premium = (block_price - daily_close) / daily_close
```

**数据来源**：`tushare_data/block_trade` → 37 个分区，但样本文件为空
**覆盖**：数据稀疏
**计算复杂度**：高，需匹配成交日期
**状态**：**数据不足**，多数分区为空，暂不可用

---

## 五、数据完备性评估

### 可直接计算（数据完整）

| 因子 | 数据表 | 关键列 | 覆盖股票 | 推荐度 | 理论驱动 |
|------|--------|--------|---------|--------|---------|
| 短期反转 | daily | pct_chg | 11,046 | ★★★★★ | 动量非线性 |
| 动量多周期 | daily | close | 11,046 | ★★★★★ | 动量非线性 |
| 成交量异动 | daily, daily_basic | vol, amount, volume_ratio | 11,046 | ★★★★★ | 流动性溢价 |
| Amihud 非流动性 | daily | pct_chg, amount | 11,046 | ★★★ | 流动性溢价 |
| 流动性改善趋势 | daily_basic | turnover_rate | 11,047 | ★★★★ | 流动性溢价 |
| 资金流（增强） | moneyflow | buy_lg_amount, sell_lg_amount | 11,022 | ★★★★★ | 流动性溢价 |
| 融资融券（增强） | margin_detail | rzye, rzmre | 10,999 | ★★★★ | 流动性溢价 |
| 业绩惊喜 | forecast + fina_indicator | p_change_min/max, netprofit_yoy | 10,962 | ★★★★★ | 成长预期+IVOL |
| size×growth 交互 | daily_basic + fina_indicator | total_mv, q_sales_yoy | 10,962 | ★★★★★ | 成长预期 |
| 条件性 IVOL | daily + fina_indicator + income | pct_chg, q_sales_yoy, rd_exp | 10,962 | ★★★★ | 异质波动率 |
| R&D 投入占比 | income | rd_exp, total_revenue | 10,962 | ★★★★ | 成长预期 |
| 质量因子增强 | fina_indicator | roe, grossprofit_margin, netprofit_margin | 10,962 | ★★★★ | — |
| 成长因子增强 | fina_indicator | q_sales_yoy, netprofit_yoy, op_yoy | 10,962 | ★★★★ | 成长预期 |
| 价值因子增强 | daily_basic | pe_ttm, pb, ps_ttm, dv_ttm | 11,047 | ★★★★ | — |

### 可计算但覆盖不完整

| 因子 | 数据表 | 覆盖 | 缺口 |
|------|--------|------|------|
| 筹码集中度 | cyq_perf | 5,477 只（~50%） | 另一半股票无数据，需 fallback |
| 涨跌停 | stk_limit | 13,158 日分区 | 按日期分区需合并，计算复杂 |
| 股东户数变化 | stk_holdernumber | 148 报告期分区 | 按季度更新，频率低 |
| 北向资金 | ccass_hold_detail | ~1,500 只（仅港股通） | 大量中小盘不在港股通范围 |

### 数据不足，暂不可用

| 因子 | 数据表 | 问题 |
|------|--------|------|
| 大宗交易溢价 | block_trade | 37 个分区，样本为空 |
| 龙虎榜机构买卖 | top_inst | 1,827 分区，但数据按日分区稀疏 |
| 高管增减持 | stk_holdertrade | 仅 37 个分区，覆盖极少 |
| 分析师预期 | 无 | tushare_data 无 analyst 预期数据 |
| 社交媒体情绪 | 无 | tushare_data 无舆情数据 |

---

## 六、推荐因子体系（中小盘增强版）

基于中小盘因子有效性和主力四步运作模型，建议在 V4 基础上增加阶段识别因子并调整权重：

### 建议新增因子

| 新增因子 | 建议权重 | 数据状态 | 识别阶段 | 理论驱动 | 计算优先级 |
|---------|---------|---------|---------|---------|-----------|
| 短期反转（5D/10D） | 0.15 | 数据完备 | 洗盘结束→拉升 | 动量非线性 | P0 |
| 成交量异动 | 0.10 | 数据完备 | 吸筹/拉升 | 流动性溢价 | P0 |
| 动量加速度（1M vs 3M） | 0.08 | 数据完备 | 拉升强度 | 动量非线性 | P0 |
| size×growth 交互 | 0.10 | 数据完备 | 选股维度 | 成长预期 | P0 |
| 条件性 IVOL | 0.08 | 多表组合 | 全阶段 | 异质波动率 | P1 |
| R&D 投入占比 | 0.05 | 数据完备 | 选股维度 | 成长预期 | P1 |
| 流动性改善趋势 | 0.05 | 数据完备 | 吸筹→拉升拐点 | 流动性溢价 | P1 |
| accumulation_score | 0.10 | 多表组合 | 吸筹阶段 | — | P1 |
| distribution_score | -0.10（负权重） | 多表组合 | 出货阶段（卖出） | — | P1 |
| shakeout_score | 0.05 | daily+moneyflow | 洗盘结束（加仓） | — | P2 |

### 建议权重调整

| 因子 | V4 权重 | 建议权重 | 调整理由 |
|------|--------|---------|---------|
| momentum | 0.25 | 0.12 | 12M 动量在出货阶段给出错误信号，需拆分+阶段过滤 |
| quality | 0.20 | 0.12 | 保持，但降低以容纳新因子 |
| earnyld | 0.20 | 0.12 | 保持，降低以容纳新因子 |
| size | -0.05 | -0.10 | 中小盘策略看空大市值，但纯 size 被 size×growth 交互替代部分 |
| growth | 0.15 | 0.08 | 独立 growth 降权，其信息被 size×growth 交互和 R&D 投入吸收 |
| resvol | -0.10 | 0.00 | 被**条件性 IVOL** 完全替代——传统 resvol 一视同仁看空是错误的 |
| **新增** 短期反转 | — | 0.15 | 洗盘后反弹信号 |
| **新增** 成交量异动 | — | 0.10 | 吸筹/拉升启动信号 |
| **新增** 动量加速度 | — | 0.08 | 识别拉升加速阶段 |
| **新增** size×growth 交互 | — | 0.10 | 小盘成长股才是真正的 Alpha 源 |
| **新增** 条件性 IVOL | — | 0.08 | 区分"有催化的高波动"vs"纯博傻的高波动" |
| **新增** R&D 投入占比 | — | 0.05 | 硬科技中小盘的估值修复领先指标 |
| **新增** 流动性改善趋势 | — | 0.05 | 流动性拐点 = 拉升启动前兆 |
| **新增** accumulation_score | — | 0.10 | 直接度量主力吸筹程度 |
| **新增** distribution_score | — | -0.10 | 识别出货，避免追高 |
| **新增** shakeout_score | — | 0.05 | 确认洗盘而非真跌 |

### 推荐权重总和

```
beta:           -0.03
momentum:        0.12  (12M)
mom_accel:       0.08  (1M vs 3M 加速度 → 拉升确认)
reversal:        0.15  (5D/10D 短期反转 → 洗盘结束)
earnyld:         0.12
quality:         0.12
growth:          0.08  (独立 growth 降权)
btop:            0.10
resvol:          0.00  (被条件性 IVOL 替代)
size:           -0.10  (被 size×growth 交互替代部分)
leverage:       -0.03
liquidity:       0.03
nlsize:          0.00
moneyflow:       0.10
chipcost:        0.07
northbound:      0.05
margin:          0.05
vol_anomaly:     0.10  (成交量异动 → 吸筹/拉升识别)
surprise:        0.05  (业绩惊喜 → 降权，因常配合出货)
size_growth:     0.10  (size×growth 交互 → 小盘成长 Alpha)
cond_ivol:       0.08  (条件性 IVOL → 有催化的高波动=正 Alpha)
rd_ratio:        0.05  (R&D 投入占比 → 估值修复领先指标)
liq_improve:     0.05  (流动性改善趋势 → 拉升前兆)
accumulation:    0.10  (吸筹识别 → 买入信号)
distribution:   -0.10  (出货识别 → 卖出信号)
shakeout:        0.05  (洗盘确认 → 加仓信号)
```

---

## 七、实施路径

### Phase 1：零成本因子（仅需 daily 表）

以下因子仅需 `daily` 表数据，可立即实施：

1. **短期反转**：`pct_chg.rolling(5/10).sum()` 取负
2. **动量加速度**：`close.pct_change(21) - close.pct_change(63)/3`
3. **成交量异动**：`vol / vol.rolling(20).mean()`
4. **Amihud 非流动性**：`abs(pct_chg) / amount`

实施方式：修改因子生成脚本，在 `barra-cne5-factors` 目录的 CSV 中增加 `reversal_5d`, `mom_accel`, `vol_ratio`, `amihud` 列。

### Phase 2：中成本因子（需加载其他表）

5. **业绩惊喜**：加载 `forecast` + `fina_indicator`，匹配 ann_date
6. **资金流增强**：加载 `moneyflow`，计算多周期大单净买入比
7. **融资融券增强**：加载 `margin_detail`，计算融资余额变化率
8. **size×growth 交互**：加载 `daily_basic`（total_mv）+ `fina_indicator`（q_sales_yoy）
9. **条件性 IVOL**：加载 `daily` + `fina_indicator` + `income`（rd_exp），判断是否有基本面催化
10. **R&D 投入占比**：加载 `income` → rd_exp / total_revenue
11. **流动性改善趋势**：加载 `daily_basic` → turnover_rate，计算短期/长期均值拐点

### Phase 3：阶段识别因子（多表组合，P1/P2 优先级）

8. **accumulation_score（吸筹识别）**：组合 daily + moneyflow + cyq_perf + margin_detail + stk_holdernumber，5 个子信号加权
9. **distribution_score（出货识别）**：组合 daily + moneyflow + cyq_perf + margin_detail + stk_holdernumber，量价背离+大单流出+筹码分散
10. **shakeout_score（洗盘识别）**：组合 daily + moneyflow + cyq_perf，缩量下跌+筹码不散

### Phase 4：高成本因子（覆盖不完整或需额外数据）

12. **筹码集中度**：加载 `cyq_perf`（50% 覆盖），缺失股票用 fallback
13. **涨跌停统计**：加载 `stk_limit`（按日分区），合并计算 20 日涨停次数
14. **股东户数**：加载 `stk_holdernumber`（按季分区），计算环比变化

### Phase 5：外部数据增强（tushare_data 不可用）

15. **Level 2 逐笔数据**：东方财富/Wind Level2，识别拆单和挂单行为
16. **委托队列快照**：东方财富 Level2，识别压盘/托盘
17. **舆情热度**：东方财富股吧/雪球，识别出货配合的利好消息
18. **分析师评级**：Wind/iFinD，识别配合出货的"买入"评级

### 基金范围扩展

- **当前**：`barra-cne5v2-factors_csi300_0608`（300 只沪深 300）
- **建议**：使用 `barra-cne5-factors`（5,181 只全市场），过滤条件：
  - `total_mv < 500 亿`（排除大盘股）
  - `turnover_rate > 0.5%`（排除流动性极差的股票）
  - `listed_days > 250`（排除次新股）
  - `is_st == 0`（排除 ST 股）
  - `missing_factor_count <= 3`（排除因子缺失过多的股票）

---

## 八、关键风险与注意事项

1. **小盘股流动性风险**：过滤 `turnover_rate < 0.3%` 的股票，避免无法成交
2. **因子过拟合风险**：新增因子必须在样本外验证 IC/IR，不能仅凭历史表现调参
3. **交易成本**：中小盘交易成本（冲击成本 + 滑点）远高于大盘股，回测中需设置合理滑点模型（建议 0.3%~0.5%）
4. **涨跌停限制**：中小盘涨停/跌停频繁，回测中需检查 `stk_limit` 数据，涨停板无法买入
5. **ST 退市风险**：必须排除 ST 股票（`is_st == 1`）
6. **因子覆盖缺口**：cyq_perf 仅覆盖 50% 的股票，缺失部分需用 0 值或均值填充
7. **数据频率不一致**：fina_indicator 按季度更新，daily 按日更新，需注意前视偏差（必须使用 ann_date 而非 end_date）
8. **"主力"假设的局限**：并非所有中小盘都有主力运作，阶段识别因子在无主力股票上产生噪音。应配合市值和换手率过滤（市值 30~200 亿、换手率 1%~8% 是主力运作最活跃的区间）
9. **主力对倒的干扰**：主力可通过自买自卖制造虚假成交量，但**无法伪造资金流方向**（大单净流入/流出是对倒无法改变的）和**股东户数变化**（过户登记是物理事实）
10. **规模溢价 vs 流动性溢价的混淆**：纯小市值因子（size）在 2017 年后显著减弱，但流动性溢价依然存在。用流动性指标替代纯市值指标可避免"小市值陷阱"——市值极小但无流动性的股票（如僵尸股）不应被选中
11. **条件性 IVOL 的滞后性**：基本面催化事件的公告日与 IVOL 变化存在时差，需使用 `ann_date`（公告日）而非 `end_date`（报告期）避免前视偏差
12. **size×growth 交互的周期性**：小盘成长股溢价在扩张周期极强、紧缩周期极弱，需结合宏观因子（流动性、信用利差）动态调整交互因子权重

---

## 九、数据表详细清单（因子相关）

| 表名 | 股票数 | 大小 | 关键列 | 可支持的因子 | 理论驱动 |
|------|--------|------|--------|------------|---------|
| daily | 11,046 | — | pct_chg, close, vol, amount | 反转, 动量, 波动率, 成交量异动, Amihud, IVOL | 动量非线性, 流动性溢价 |
| adj_factor | 11,053 | — | adj_factor | 复权计算 | — |
| daily_basic | 11,047 | — | turnover_rate, pe_ttm, pb, ps_ttm, total_mv, circ_mv | 价值, 市值, 流动性, 流动性改善趋势 | 流动性溢价, 成长预期 |
| fina_indicator | 10,962 | — | roe, grossprofit_margin, netprofit_yoy, q_sales_yoy, debt_to_assets | 质量, 成长, 杠杆, size×growth | 成长预期 |
| income | 10,962 | — | revenue, n_income, rd_exp, total_revenue | R&D 投入占比, 营收增长 | 成长预期 |
| moneyflow | 11,022 | ~3GB | buy_lg_amount, sell_lg_amount, net_mf_amount | 资金流 | 流动性溢价 |
| margin_detail | 10,999 | ~613MB | rzye, rzmre, rzche | 融资融券 | 流动性溢价 |
| cyq_perf | 5,477 | — | cost_5pct~95pct, winner_rate | 筹码成本 | 流动性溢价 |
| cyq_chips | 5,476 | — | price, percent | 筹码分布（精细） | 流动性溢价 |
| forecast | 10,962 | — | p_change_min, p_change_max | 业绩惊喜 | 成长预期+IVOL |
| stk_limit | 13,158 日 | — | up_limit, down_limit | 涨跌停 | 动量非线性 |
| stk_holdernumber | 148 期 | — | holder_num | 股东户数变化 | 流动性溢价 |
| ccass_hold_detail | 1,827 日 | — | col_shareholding | 北向资金 | 流动性溢价 |
| balancesheet | 10,962 | — | total_assets, total_liab, total_hldr_eqy | 资产负债结构 | — |
| cashflow | 10,962 | — | n_cashflow_act, free_cashflow | 现金流质量 | — |

---

*数据截至 2026-06-10，基于 /home/project/tushare-downloader/tushare_data 本地 Parquet 数据*
