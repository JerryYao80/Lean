# A股中小盘隐含波动率：CFFEX股指期权方案

## 核心发现：CFFEX MO（中证1000股指期权）是中小盘IV的直接来源

之前的分析（midsml3.md）仅关注了 SSE ETF 期权，遗漏了 `opt_daily` 中更重要的 CFFEX 股指期权数据。

---

## 一、实际数据盘点

### 已有期权数据全貌

`/home/project/tushare-downloader/tushare_data/opt_daily/` 包含两种分区：

| 分区类型 | 数量 | 内容 | 时间范围 |
|----------|------|------|---------|
| `date=` | 1,511个非空 | SSE ETF期权 + **CFFEX股指期权** + 期货 | 2020-01-02 ~ 2026-06-05 |
| `year=` | 37个 | 全交易所快照（含SZSE），但每年仅1天 | 无实用价值 |

### CFFEX 股指期权（date= 分区中的核心数据）

| 品种 | 代码前缀 | 标的 | 历史合约总数 | 日均活跃合约 | 日成交量 | 日OI | 对中小盘价值 |
|------|---------|------|------------|-----------|---------|------|------------|
| **MO** | MO | **中证1000指数** | 2,992 | ~285 | 40万手 | 41万手 | ⭐⭐⭐ **直接来源** |
| HO | HO | 沪深300指数 | 2,142 | ~155 | 5万手 | 11万手 | 大盘IV |
| IO | IO | 沪深300指数(旧) | 4,620 | ~229 | 15万手 | 20万手 | 大盘IV(老) |

### SSE ETF 期权（date= 分区中）

| 品种 | 标的 | 日均合约 | 日成交量 | 状态 |
|------|------|---------|---------|------|
| 50ETF期权 | 上证50ETF | ~80 | ~40万 | 已有IV管线 |
| 300ETF期权 | 沪深300ETF | ~150 | ~200万 | 已有IV管线 |
| 500ETF期权 | 中证500ETF | ~50 | ~30万 | 已有IV管线 |

### SZSE ETF 期权（year= 分区中，不可用）

| 问题 | 说明 |
|------|------|
| 日线数据缺失 | `date=` 分区无SZSE数据，`year=` 分区每年仅1天快照 |
| 缺opt_basic映射 | 合约代码 `9000xxxxx.SZ` 不自解释，无法确定标的是哪个ETF |
| 结论 | **不可用于计算IV，但也不需要**——CFFEX MO 完全覆盖中小盘 |

---

## 二、为什么 MO（中证1000股指期权）是中小盘IV的最佳来源

### 中证1000指数成分股特征

- 成分股：1,000只，市值集中在 **100~500亿**
- 覆盖了 midsml2.md 中"中小盘"的核心范围（30~500亿）
- 与沪深300成分股无重叠，与中证500仅有少量重叠

### MO 期权流动性

2026-06-05 实际数据：

```
MO(中证1000):
  Total contracts: 294
  Active (vol>0): 285
  Total volume: 404,555 手
  Total OI: 415,488 手
  By expiry:
    2606: 82 contracts, vol=346,829, OI=256,202
    2607: 52 contracts, vol=35,133,  OI=61,856
    2608: 44 contracts, vol=4,314,   OI=7,703
    2609: 39 contracts, vol=11,639,  OI=47,328
    2612: 37 contracts, vol=4,620,   OI=29,809
    2703: 31 contracts, vol=2,020,   OI=12,014
```

日均 40 万手成交量、41 万手 OI——流动性充裕，足以计算可靠的 ATM IV 和偏斜。

### 与其他方案的对比

| 数据源 | 标的 | 市值覆盖 | 能否计算IV | 对中小盘适用性 |
|--------|------|---------|-----------|--------------|
| SSE 50ETF期权 | 上证50 | > 2000亿 | ✅ 已有 | ❌ 纯大盘 |
| SSE 300ETF期权 | 沪深300 | > 500亿 | ✅ 已有 | ❌ 大盘偏中 |
| SSE 500ETF期权 | 中证500 | 200~800亿 | ✅ 已有 | ⚠️ 中盘 |
| **CFFEX MO期权** | **中证1000** | **100~500亿** | ✅ **待实现** | ⭐ **精准覆盖** |
| SZSE 创业板ETF期权 | 创业板指 | 100~500亿 | ❌ 缺日线 | ⭐ 理想但缺数据 |
| SZSE 中证1000ETF期权 | 中证1000 | 100~500亿 | ❌ 缺日线 | ⭐ 与MO重叠 |
| A50期货期权 | 富时A50 | > 2000亿 | ❌ 无数据 | ❌ 纯大盘 |

---

## 三、CFFEX 合约自解释，无需 opt_basic

### 合约编码规则

CFFEX 股指期权的 ts_code 直接编码了所有合约信息：

```
MO2606-C-5100.CFX
│  │    │   └── 行权价（5100点）
│  │    └── C=Call认购, P=Put认沽
│  └── 到期月份（2026年06月）
└── 品种代码：MO=中证1000, HO=沪深300(新), IO=沪深300(旧)
```

### 从 opt_daily 反推合约信息

无需调用 tushare `opt_basic` API，直接解析 ts_code 即可：

```python
import re

def decode_cffex_option(ts_code: str) -> dict:
    """解析 CFFEX 股指期权合约代码"""
    # MO2606-C-5100.CFX -> ('MO', '2606', 'C', '5100')
    match = re.match(r'([A-Z]+)(\d{4})-([CP])-(\d+\.?\d*)\.CFX', ts_code)
    if not match:
        return None

    prefix, expiry, cp, strike = match.groups()

    underlying_map = {
        'MO': '中证1000指数',
        'HO': '沪深300指数',
        'IO': '沪深300指数',
    }

    # 解析到期月份
    year = 2000 + int(expiry[:2])
    month = int(expiry[2:4])

    return {
        'ts_code': ts_code,
        'opt_type': '股指期权',
        'underlying': underlying_map.get(prefix, prefix),
        'prefix': prefix,
        'expiry_year': year,
        'expiry_month': month,
        'call_put': 'Call' if cp == 'C' else 'Put',
        'exercise_price': float(strike),
    }
```

### 已解码合约统计

从 opt_daily 全量扫描解码：

| 品种 | 合约数 | 标的 | 行权价范围 | 到期月份 |
|------|--------|------|-----------|---------|
| MO | 2,992 | 中证1000 | 3,800 ~ 7,200 | 2022-08 ~ 2027-03 |
| HO | 2,142 | 沪深300 | 3,200 ~ 4,400 | 2022-07 ~ 2027-03 |
| IO | 4,620 | 沪深300 | 3,000 ~ 4,800 | 2020-01 ~ 2027-03 |

---

## 四、计算中证1000隐含波动率的实施方案

### 架构：复用现有 IV 管线

现有的 `Scripts/ashare_implied_volatility.py` 已实现：
- Black-Scholes Newton-Raphson IV 求解
- CBOE VIX-like 模型无关隐含波动率
- ATM IV / 25Δ skew / VIX 计算
- InfluxDB 写入 + LEAN CSV 导出

只需扩展支持 CFFEX 数据源。

### 所需数据

| 数据 | 来源 | 列 | 状态 |
|------|------|-----|------|
| 期权日线 | `opt_daily/date=YYYYMMDD/` | `ts_code, trade_date, close, settle, vol, oi` | ✅ 1,511天 |
| 标的指数 | `index_daily/` | 中证1000指数收盘价 | ✅ 需确认ts_code |
| 无风险利率 | `shibor/` | `1m, 3m, 6m, 1y` | ✅ 完备 |
| 合约信息 | 从 ts_code 解析 | 行权价、到期日、Call/Put | ✅ 自解释 |

### 实施步骤

#### Step 1：确认中证1000指数数据

```python
# 中证1000指数 ts_code = '000852.SH'
# 需验证 index_daily 中是否有该指数
import pandas as pd
df = pd.read_parquet('/home/project/tushare-downloader/tushare_data/index_daily/...')
# 检查 000852.SH 是否存在
```

#### Step 2：加载 CFFEX MO 期权数据

```python
def load_mo_options(trade_date: str) -> pd.DataFrame:
    """加载指定日期的 MO（中证1000）期权数据"""
    df = pd.read_parquet(f'opt_daily/date={trade_date}/data.parquet')
    cffex = df[df['exchange'] == 'CFFEX']
    mo = cffex[cffex['ts_code'].str.startswith('MO')].copy()

    # 解析合约信息
    decoded = mo['ts_code'].apply(decode_cffex_option)
    mo_info = pd.DataFrame(decoded.tolist())
    mo = pd.concat([mo.reset_index(drop=True), mo_info], axis=1)

    return mo
```

#### Step 3：计算 ATM IV / Skew / VIX

```python
def compute_mo_iv(trade_date: str):
    """计算中证1000股指期权的隐含波动率"""
    # 1. 加载数据
    options = load_mo_options(trade_date)
    spot = get_index_close('000852.SH', trade_date)  # 中证1000指数收盘价
    risk_free_rate = get_shibor(trade_date, tenor='1m') / 100  # SHIBOR 1M

    # 2. 筛选活跃合约
    active = options[options['vol'] > 0]

    # 3. 找近月和次近月合约
    near_term, next_term = find_near_next_expiry(active)

    # 4. 计算 ATM IV（Newton-Raphson）
    atm_iv = compute_atm_iv(near_term, spot, risk_free_rate)

    # 5. 计算 25Δ skew
    iv_call_25 = compute_delta_iv(near_term, spot, risk_free_rate, delta=0.25, cp='C')
    iv_put_25 = compute_delta_iv(near_term, spot, risk_free_rate, delta=0.25, cp='P')
    skew = iv_call_25 - iv_put_25

    # 6. 计算 VIX（CBOE 方法）
    vix = compute_vix_for_term(near_term, next_term, spot, risk_free_rate)

    return {
        'trade_date': trade_date,
        'atm_iv': atm_iv,
        'iv_call_25delta': iv_call_25,
        'iv_put_25delta': iv_put_25,
        'skew': skew,
        'vix': vix,
        'option_count': len(active),
    }
```

#### Step 4：导出 LEAN 格式 CSV

```python
# 输出路径：Data/alternative/ashare-implied-volatility/sse/daily/000852.csv
# 列：trade_date, atm_iv, iv_call_25delta, iv_put_25delta, skew, vix, ...
```

#### Step 5：β 调整映射到个股

```python
def map_iv_to_stock(stock_code, stock_beta, stock_total_mv, mo_iv, csi300_iv):
    """将指数IV映射到个股预期波动率"""
    # 选择基准IV：中小盘用中证1000 IV，大盘用沪深300 IV
    if stock_total_mv < 500e8:  # < 500亿 → 中小盘
        base_iv = mo_iv           # 中证1000 IV
    else:
        base_iv = csi300_iv       # 沪深300 IV

    # 系统性预期波动率 = |β| × 指数IV
    systematic_vol = abs(stock_beta) * base_iv

    # 特质波动率（从历史数据估计）
    idio_vol = estimate_idio_vol(stock_code)  # Parkinson 或 GARCH

    # 总预期波动率
    expected_vol = np.sqrt(systematic_vol**2 + idio_vol**2)
    return expected_vol
```

---

## 五、最终产出：三级波动率体系

| 层级 | 数据源 | 产出 | 用途 |
|------|--------|------|------|
| **指数IV** | CFFEX MO/HO/IO + SSE ETF期权 | 中证1000 ATM IV / skew / VIX | 中小盘市场情绪指标 |
| **个股预期波动率** | 指数IV + β + 特质波动率 | 每只股票的前瞻性波动率估计 | 条件性IVOL因子输入 |
| **已实现波动率** | daily OHLC | Parkinson / Garman-Klass | 波动率分解、IV-RV差（风险溢价） |

### 与 midsml2.md 因子体系的衔接

| 因子 | 当前输入 | 改进后输入 | 改进效果 |
|------|---------|-----------|---------|
| 条件性 IVOL | `pct_chg.rolling(20).std()` | β×MO_IV + Parkinson特质 | **前瞻性**：从"已发生"变为"市场预期" |
| resvol | `rolling(20).std()` | Parkinson/GK 估计器 | **更精确**：利用OHLC信息 |
| accumulation_score | 无IV信号 | + MO VIX 上升信号 | 捕捉市场恐慌→吸筹窗口 |
| distribution_score | 量价背离 | + MO skew 极端偏斜信号 | 捕捉尾部风险→出货加速 |
| **新增** IV-RV差 | — | MO_IV - Parkinson_RV | 波动率风险溢价，正=市场过度恐慌 |

---

## 六、SZSE 期权数据缺口的处理

### 现状

- SZSE 期权日线数据**不在** `date=` 分区中（仅 `year=` 快照，无实用价值）
- `opt_basic` API 仅有 SSE 数据，SZSE/CFFEX 的合约信息缺失
- tushare API 代理服务当前不可用，无法补充下载

### 为什么不影响实施

1. **CFFEX MO 完全覆盖中小盘**：中证1000股指期权的标的就是中小盘指数，与 midsml2.md 的目标市值区间完全吻合
2. **CFFEX 合约自解释**：ts_code 编码了行权价、到期日、Call/Put，无需 opt_basic
3. **SZSE ETF期权是冗余的**：创业板ETF期权和中证1000ETF期权的标的信息，MO股指期权已经提供了

### 后续补充（可选）

当 tushare API 恢复后，可补充：

1. **SZSE opt_basic**：`pro.opt_basic(exchange='SZSE')` → 映射 `9000xxxxx.SZ` 到具体ETF
2. **SZSE opt_daily**：按日期重新下载 SZSE 期权日线 → 创业板ETF IV（与MO互补）
3. **CFFEX opt_basic**：`pro.opt_basic(exchange='CFFEX')` → 获取合约乘数等元数据

---

## 七、数据完备性总结

| 数据 | 来源 | 状态 | 用途 |
|------|------|------|------|
| CFFEX MO 期权日线 | `opt_daily/date=` (1,511天) | ✅ 完备 | **中证1000 IV 计算** |
| CFFEX HO/IO 期权日线 | `opt_daily/date=` (1,511天) | ✅ 完备 | 沪深300 IV（已有管线可替代） |
| SSE ETF 期权日线 | `opt_daily/date=` (1,009天) | ✅ 完备 | 50ETF/300ETF/500ETF IV（已有） |
| 中证1000指数点位 | `index_daily/` | ⚠️ 需确认 000852.SH | MO 期权标的价格 |
| 沪深300指数点位 | `index_daily/` | ✅ 已有 | HO/IO 期权标的价格 |
| SHIBOR | `shibor/` (37年分区) | ✅ 完备 | 无风险利率 |
| 个股日线OHLC | `daily/` (11,046只) | ✅ 完备 | β计算 + 特质波动率 |
| 个股市值 | `daily_basic/` (11,047只) | ✅ 完备 | 选择基准IV（MO vs HO） |
| SZSE 期权日线 | 缺失 | ❌ 无日线数据 | 可选补充（非必需） |
| 个股期权 | — | ❌ 中国不存在 | 无法直接计算个股IV |

---

*数据截至 2026-06-11，基于 /home/project/tushare-downloader/tushare_data 本地 Parquet 数据*
