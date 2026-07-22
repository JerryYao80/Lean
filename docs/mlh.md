# ETF波动率择时策略（利用期权IV信号）

## 📊 期权波动率套利策略对A股投资者的实际价值

### ✅ 核心价值：信号价值，而非交易策略

针对"我不能买期权，期权波动率套利策略对我买A股个股或A股ETF有何用处"的问题，答案是：**利用期权市场的IV信号来指导您的ETF/股票交易**，这是聪明钱（机构）情绪的直接指标，比技术指标更领先。

---

## 🎯 对A股投资者的实际用途

### 1. 择时信号（您唯一需要的部分）

| IV信号 | 含义 | 您的操作 |
|--------|------|---------|
| **IV-RV价差 > 15%** | 市场过度恐慌（期权市场定价了过多风险） | **买入ETF/股票**（买在恐慌时） |
| **IV-RV价差 < 5%** | 市场过度乐观（期权定价风险太低） | **减仓ETF/股票**（卖在贪婪时） |
| **Put Skew > 20%** | 极端恐慌（看跌期权被抢购） | **强烈买入信号** |
| **IV Skew Surface < -2%** | 防御性增强（虚值Put昂贵） | **谨慎，减仓** |

---

### 2. 您实际可以使用的策略

**方案A：ETF择时策略（推荐）**
```
数据来源：期权市场IV（已预计算好的CSV）
交易标的：510050/510300 ETF（您能买）
逻辑：
  - IV高 → 期权市场恐慌 → 买入ETF
  - IV低 → 期权市场乐观 → 减持ETF
```

**方案B：个股择时辅助**
```
观察标的：沪深300期权IV（代表大盘情绪）
操作：贵州茅台/招商银行等蓝筹股
逻辑：
  - 大盘IV飙升 → 市场恐慌 → 买入优质个股
  - 大盘IV低迷 → 市场过热 → 减持
```

---

### 3. 为什么这有效？

**期权市场是聪明钱聚集地**：
- 机构投资者用期权对冲风险
- IV反映市场对未来波动的预期
- IV-RV价差 = 恐慌溢价/乐观折价

**您不需要交易期权**，只需要**读取期权市场的信号**来指导股票/ETF交易。

---

## 📁 实际可用策略

### AShareETFVolatilityTimingStrategy

**特点**：
- ✅ 只交易ETF（510050）
- ✅ 使用预计算的IV数据（CSV文件）
- ✅ 无需实时option chain
- ✅ 基于IV信号择时

**文件位置**：
- 策略代码：`Algorithm.CSharp/AShareETFVolatilityTimingStrategy.cs`
- 配置文件：`Launcher/config/config-ashare-etf-vol-timing-backtest.json`
- IV数据：`Data/alternative/ashare-implied-volatility/sse/daily/510050.csv`（已生成49天数据）

---

## 🚀 运行命令

```bash
cd Launcher
dotnet QuantConnect.Lean.Launcher.dll --config config/config-ashare-etf-vol-timing-backtest.json
```

---

## 💡 策略核心逻辑

**择时逻辑**：
```
IV > 25% + Put Skew > 5% → 市场恐慌 → 买入ETF（STRONG BUY）
IV < 15% → 市场过度乐观 → 减持ETF（REDUCE）
IV-RV价差 > 5% → 风险溢价高 → 加仓（BUY）
其他情况 → HOLD
```

### 信号参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `IvHighThreshold` | 25% | ATM IV > 25% → 高波动率regime（恐慌） |
| `IvLowThreshold` | 15% | ATM IV < 15% → 低波动率regime（乐观） |
| `SkewExtremeThreshold` | 5% | Put skew > 5% → 恐慌 |
| `SurfaceSkewThreshold` | -2% | Surface skew < -2% → 陡峭put wing（防御） |

---

## 🎯 对您的实际价值

**这个策略解决了核心问题**：
- 期权市场IV = 机构情绪指标
- **不需要交易期权**
- 只需要**读取IV信号**来指导ETF买卖
- IV高（恐慌）→ 买ETF
- IV低（贪婪）→ 卖ETF

这是**利用期权市场信息来增强股票/ETF交易**的典型应用。

---

## 📋 Tushare中的情绪与宏观数据

### 已发现的数据类型

#### 1. 情绪/新闻数据

| 数据表 | 行数 | 字段 | 用途 |
|-------|------|------|------|
| **cctv_news** | 100 | date, title, content | 新闻联播情绪分析、政策风向判断 |
| **dc_hot** | 目录存在 | 数据热点（待下载） | 市场热点追踪 |

#### 2. 宏观经济数据

| 数据表 | 行数 | 字段数 | 关键指标 | 用途 |
|-------|------|--------|---------|------|
| **cn_pmi** | 252 | 65 | 制造业PMI、非制造业PMI | 经济景气度判断 |
| **cn_cpi** | 504 | 13 | 全国/城镇/农村CPI同比环比 | 通胀预期分析 |
| **cn_m** | 576 | 10 | M0/M1/M2及同比环比 | 流动性判断 |
| **cn_gdp** | 目录存在 | GDP数据 | 经济增长趋势 |
| **cn_ppi** | 目录存在 | PPI数据 | 工业品价格趋势 |

#### PMI细分指标（cn_pmi）

```
PMI010000 - 制造业PMI综合指数
PMI010100 - 生产指数
PMI010200 - 新订单指数
PMI010300 - 新出口订单指数
PMI010400 - 在手订单指数
PMI010500 - 产成品库存指数
PMI010600 - 采购量指数
PMI010700 - 进口指数
PMI010800 - 出厂价格指数
PMI010900 - 主要原材料购进价格指数
PMI011000 - 原材料库存指数
PMI011100 - 从业人员指数
PMI020000 - 非制造业PMI综合指数
```

---

## 🎯 多因子择时系统构建

### IV+宏观+情绪复合因子

| 因子类型 | 数据源 | 信号逻辑 | 权重建议 |
|---------|--------|---------|---------|
| **IV情绪因子** | 期权IV数据 | IV高→恐慌→买入 | 40% |
| **PMI景气因子** | cn_pmi | PMI<50→衰退→减仓 | 25% |
| **M2流动性因子** | cn_m | M2增速升→宽松→买入 | 20% |
| **新闻情绪因子** | cctv_news | 政策利好→加仓 | 15% |

### 择时逻辑示例

```python
# 复合择时信号
def generate_composite_signal(iv_signal, pmi, m2_growth, news_sentiment):
    """
    iv_signal: 'BUY'/'HOLD'/'REDUCE' (来自期权IV)
    pmi: 制造业PMI指数 (cn_pmi.PMI010000)
    m2_growth: M2同比增速 (cn_m.m2_yoy)
    news_sentiment: 新闻情绪得分 (从cctv_news文本分析)
    """
    
    # IV信号权重最高
    if iv_signal == 'STRONG BUY':
        return 'STRONG BUY'
    
    # PMI景气度判断
    if pmi < 50:  # 经济衰退期
        if iv_signal == 'BUY':
            return 'HOLD'  # IV恐慌但PMI差 → 观望
        else:
            return 'REDUCE'  # PMI差且IV不恐慌 → 减仓
    
    # M2流动性判断
    if m2_growth > 10:  # 货币宽松
        if iv_signal == 'BUY' or news_sentiment > 0.5:
            return 'BUY'  # 流动性+情绪好 → 加仓
    
    # 复合信号
    return iv_signal  # 默认跟随IV信号
```

---

## 📂 数据路径

所有Tushare数据位于：
```
/home/project/tushare-downloader/tushare_data_v2/
  ├── cctv_news/         (新闻联播)
  ├── cn_pmi/            (PMI)
  ├── cn_cpi/            (CPI)
  ├── cn_m/              (货币供应量)
  ├── cn_gdp/            (GDP)
  ├── cn_ppi/            (PPI)
```

数据格式：Parquet分区存储（按year/month分区）