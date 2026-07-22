# 波动率曲面（Volatility Surface）实现文档

## 概述

本项目实现了基于 A 股 ETF 期权的隐含波动率曲面（Implied Volatility Surface），用于捕捉期权市场对未来波动率的预期分布特征。

## 数据源

### 主要数据表

- **opt_daily**: tushare 期权日线行情数据
  - 路径: `/home/project/tushare-downloader/tushare_data_v2/opt_daily/`
  - 分区: `trade_date=YYYYMMDD/data.parquet`
  - 关键字段: `ts_code`, `settle` (结算价), `close` (收盘价), `vol` (成交量)

- **opt_basic**: tushare 期权合约基本信息
  - 路径: `/home/project/tushare-downloader/tushare_data_v2/opt_basic/data.parquet`
  - 关键字段: `ts_code`, `call_put` (C/P), `exercise_price` (行权价), `maturity_date` (到期日)

- **fund_daily**: ETF 基金日线数据（用于获取标的价格）
  - 路径: `/home/project/tushare-downloader/tushare_data_v2/fund_daily/`
  - 关键字段: `ts_code`, `close` (收盘价)

### 标的资产

当前实现主要支持:
- **50ETF (510050.SH)**: 上证 50ETF 期权
- 可扩展至 300ETF (510300.SH) 等其他 ETF 期权

## 实现原理

### 1. 隐含波动率计算（Black-Scholes 模型反推）

使用 Black-Scholes 期权定价模型，通过牛顿迭代法反推隐含波动率：

```python
def bs_call(S, K, T, r, sigma):
    """Black-Scholes 看涨期权定价"""
    d1 = (np.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
    d2 = d1 - sigma*np.sqrt(T)
    return S*norm.cdf(d1) - K*np.exp(-r*T)*norm.cdf(d2)

def bs_vega(S, K, T, r, sigma):
    """Vega: 期权价格对波动率的敏感度"""
    d1 = (np.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
    return S*norm.pdf(d1)*np.sqrt(T)

def iv_newton(price, S, K, T, r, cp):
    """牛顿迭代法求解隐含波动率"""
    sigma = 0.3  # 初始猜测值
    for _ in range(50):
        model_price = bs_call(S, K, T, r, sigma) if cp == 'C' else bs_put(S, K, T, r, sigma)
        diff = model_price - price
        if abs(diff) < 1e-8:
            return sigma
        vega = bs_vega(S, K, T, r, sigma)
        if vega < 1e-12:
            return None
        sigma -= diff / vega
        if sigma <= 0:
            sigma = 0.001
        if sigma > 5.0:
            return None
    return None
```

### 2. 波动率曲面构建

波动率曲面是三维空间中的函数: `IV = f(Strike, Time_to_Expiry)`

构建步骤:
1. **数据筛选**: 选择特定交易日的期权合约，过滤掉深度虚值（OTM）和流动性差的合约
2. **IV 计算**: 对每个有效合约计算隐含波动率
3. **网格化**: 将 (行权价, 到期时间) 空间离散化为网格
4. **插值**: 使用双线性插值或样条插值填充网格

### 3. 波动率微笑/偏斜特征

波动率曲面通常呈现以下特征:

- **波动率微笑（Volatility Smile）**: 相同到期日，不同行权价的 IV 呈现 U 形曲线
- **波动率偏斜（Volatility Skew）**: 低行权价（OTM Put）的 IV 高于高行权价（OTM Call）
- **期限结构（Term Structure）**: 不同到期时间的 ATM IV 随时间的变化

## 因子实现

### IVSkewFactor

**位置**: `Common/Factors/Volatility/IVSkewFactor.cs`

**功能**: 计算波动率偏斜因子，衡量 OTM Put 与 OTM Call 的 IV 差异

**计算方法**:
```csharp
// 25-delta risk reversal
skew = IV(25-delta Put) - IV(25-delta Call)
```

**消费场景**:
- Layer A: 作为 alpha 信号，高偏斜通常预示市场恐慌
- Layer B: 作为风险预警，偏斜异常放大时触发风控

### IVTermStructureFactor

**位置**: `Common/Factors/Volatility/IVTermStructureFactor.cs`

**功能**: 计算波动率期限结构因子，衡量近月与远月 ATM IV 的差异

**计算方法**:
```csharp
// near-term vs next-term ATM IV ratio
term_structure = IV(near-term ATM) / IV(next-term ATM)
```

**消费场景**:
- Layer A: 期限结构倒挂（近月 > 远月）通常预示短期风险
- Layer C: 作为仓位调节信号

### compute_iv_surface_skew

**位置**: `Scripts/ashare_implied_volatility.py`

**功能**: 计算波动率曲面的偏斜特征

**计算方法**:
```python
# 按到期日分组，计算每个到期日的偏斜
for dte in expiries:
    low_strike_iv = mean(IV[low quartile strikes])
    high_strike_iv = mean(IV[high quartile strikes])
    skew[dte] = low_strike_iv - high_strike_iv

# 加权平均偏斜（近月权重更高）
surface_skew = weighted_mean(skew, weights=1/dte)
```

## 可视化

### 生成的图表

**文件**: `docs/ivqumian/iv-surface-50etf-20200102.png`

**内容**: 50ETF 期权波动率曲面三维图（2020-01-02）

**特征**:
- X 轴: 行权价（Strike）
- Y 轴: 到期天数（Days to Expiry）
- Z 轴: 隐含波动率（IV%）
- 颜色: 按 IV 值映射（viridis 色图）
- 红色星号: ATM 位置

**观察到的模式**:
1. **波动率偏斜**: 低行权价（左侧）IV 明显高于高行权价（右侧）
2. **期限结构**: 近月期权 IV 波动更大，远月趋于平滑
3. **ATM 位置**: 红色星号标记的 ATM 位置处于曲面的"谷底"

### 生成代码

```python
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

# 创建网格
strikes = np.array(sorted(iv_by_strike.keys()))
dtes = np.array(sorted(iv_by_dte.keys()))
K_mesh, DTE_mesh = np.meshgrid(strikes, dtes)

# 插值 IV 曲面
IV_surface = np.zeros_like(K_mesh)
for i in range(len(dtes)):
    for j in range(len(strikes)):
        IV_surface[i, j] = iv_grid[dtes[i]][strikes[j]]

# 3D 绘图
fig = plt.figure(figsize=(12, 8))
ax = fig.add_subplot(111, projection='3d')
surf = ax.plot_surface(K_mesh, DTE_mesh, IV_surface*100, 
                       cmap='viridis', alpha=0.8)
ax.set_xlabel('Strike')
ax.set_ylabel('Days to Expiry')
ax.set_zlabel('IV (%)')
ax.set_title('50ETF Volatility Surface (2020-01-02)')
plt.colorbar(surf)
plt.savefig('docs/ivqumian/iv-surface-50etf-20200102.png', dpi=150)
```

## 应用场景

### 1. 波动率交易策略

- **做多波动率**: 当曲面整体偏低时，买入跨式组合（Straddle）
- **做空波动率**: 当曲面整体偏高时，卖出跨式组合
- **偏斜交易**: 利用偏斜异常进行风险反转（Risk Reversal）

### 2. 风险管理

- **VaR 计算**: 使用波动率曲面估计期权组合的 VaR
- **Greeks 对冲**: 基于曲面计算 Vega、Vanna、Volga 等高阶 Greeks
- **压力测试**: 模拟曲面形态变化对组合的影响

### 3. 市场情绪指标

- **VIX 替代**: ATM IV 加权平均可作为 A 股版的 VIX
- **恐慌指数**: 偏斜因子可作为市场恐慌情绪的领先指标
- **期限结构信号**: 倒挂的期限结构预示短期风险

## 性能优化

### 计算优化

1. **向量化**: 使用 NumPy 向量化 IV 计算，避免 Python 循环
2. **缓存**: 缓存历史 IV 计算结果，避免重复计算
3. **并行**: 使用多进程并行计算不同到期日的 IV

### 存储优化

1. **增量更新**: 每日只计算新增合约的 IV
2. **压缩存储**: 使用 Parquet 格式存储 IV 曲面数据
3. **分区**: 按标的资产和日期分区存储

## 扩展方向

### 1. 多标的支持

- 300ETF (510300.SH) 期权
- 创业板 ETF 期权
- 个股期权（如 50ETF 成分股）

### 2. 高级模型

- **SABR 模型**: 更精确地拟合波动率微笑
- **SVI 模型**: 参数化波动率曲面
- **随机波动率模型**: Heston 模型等

### 3. 实时计算

- 接入实时行情数据
- 流式计算 IV 曲面
- 实时监控偏斜异常

## 参考资料

1. **Black-Scholes Model**: Black, F., & Scholes, M. (1973). The Pricing of Options and Corporate Liabilities.
2. **Volatility Surface**: Gatheral, J. (2006). The Volatility Surface: A Practitioner's Guide.
3. **SABR Model**: Hagan, P. S., et al. (2002). Managing Smile Risk.
4. **tushare 文档**: https://tushare.pro/document/2?doc_id=127

## 维护说明

### 数据更新

- 每日收盘后运行 `Scripts/ashare_implied_volatility.py` 更新 IV 曲面
- 检查数据完整性，确保所有合约都有结算价

### 模型校准

- 定期检查 IV 计算结果与市场报价的偏差
- 调整牛顿迭代法的收敛阈值和最大迭代次数

### 性能监控

- 监控 IV 计算耗时，确保在合理范围内
- 检查内存使用，避免内存泄漏

---

**文档版本**: v1.0  
**最后更新**: 2024-01-XX  
**维护者**: LEAN 量化团队
