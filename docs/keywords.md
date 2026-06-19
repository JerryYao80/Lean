# 12 Strategy Family 搜索网站与关键字设计

## 搜索网站 (9个 site: 限制)

| 来源 | site: 限制 |
|------|-----------|
| arXiv q-fin | `site:arxiv.org` |
| SSRN | `site:papers.ssrn.com` |
| Quantpedia | `site:quantpedia.com` |
| QuantConnect | `site:quantconnect.com` |
| Alpha Architect | `site:alphaarchitect.com` |
| AQR | `site:aqr.com` |
| 聚宽 | `site:joinquant.com` |
| 米筐 | `site:ricequant.com` |
| 优矿 | `site:uqer.datayes.com` |

另外有12个来源前缀标记(无site:限制): arXiv, SSRN, Quantpedia, WorldQuant BRAIN, QuantConnect, Numerai, 华泰金工研报, 中信金工研报, AlphaArchitect, AQR, 聚宽, 米筐, 优矿

## 各Family搜索关键字

### 1. momentum_reversal (动量反转)

- CN: A股 动量因子 收盘价 换手率 总市值 量化策略 回测 / A股 反转因子 涨跌幅 成交量 换手率 策略 回测 / A股 截面动量 换手率 总市值 风险因子 多空策略 / 华泰 金工 动量反转因子 研报 策略 回测
- EN: cross-sectional momentum factor turnover market cap A-share backtest / short-term reversal factor volume turnover A-share strategy / momentum crash reversal factor A-share quant strategy

### 2. value_quality (价值质量)

- CN: A股 价值因子 市盈率 市净率 股息率 ROE 量化策略 回测 / A股 质量因子 ROE 毛利率 资产负债率 策略 回测 / A股 价值成长 PE PB DV ROE 多因子 策略 回测 / 中信 金工 价值质量因子 研报 策略
- EN: value factor PE PB dividend yield ROE A-share strategy backtest / quality factor gross margin current ratio debt A-share quant / value investing PE PB ROE A-share multi-factor backtest

### 3. money_flow (资金流向)

- CN: A股 资金流向 大单净流入 主力资金 量化策略 回测 / A股 资金流因子 超大单 小单 净流入 选股策略 回测 / A股 主力资金 机构资金 资金流向 策略 回测 / 华泰 金工 资金流因子 研报 策略 回测
- EN: money flow factor large order net inflow A-share strategy backtest / capital flow institutional money inflow A-share quant strategy / smart money flow factor A-share stock selection backtest

### 4. earnings_surprise (盈利惊喜)

- CN: A股 盈利惊喜 业绩预告 EPS超预期 量化策略 回测 / A股 业绩快报 净利润增长率 营收增长 选股策略 回测 / A股 PEAD 盈利公告后漂移 标准化意外盈利 策略 回测 / A股 盈利超预期因子 业绩预告 研报 策略
- EN: earnings surprise factor PEAD A-share strategy backtest / post-earnings announcement drift A-share quant strategy / standardized unexpected earnings SUE A-share backtest

### 5. chip_cost (筹码成本)

- CN: A股 筹码分布 获利盘 筹码集中度 量化策略 回测 / A股 筹码成本 均价 获利比例 选股策略 回测 / A股 筹码峰 集中度因子 支撑压力 策略 回测
- EN: chip distribution cost basis winner rate A-share strategy backtest / chip concentration factor A-share quant strategy / cost distribution support resistance A-share backtest

### 6. etf_premium (ETF折溢价)

- CN: ETF 折溢价 NAV 净值差 套利策略 回测 / A股 ETF 份额变化 净值 折价溢价 量化策略 / A股 ETF 场内溢价 份额增减 策略 回测
- EN: ETF premium discount NAV arbitrage A-share strategy backtest / ETF share creation redemption premium A-share quant / ETF NAV discount spread trading strategy A-share

### 7. sector_rotation (行业轮动)

- CN: A股 行业轮动 申万行业 动量 量化策略 回测 / A股 行业动量 行业估值 PE PB 轮动策略 回测 / A股 板块轮动 行业资金流向 策略 回测 / 华泰 金工 行业轮动 研报 策略 回测
- EN: sector rotation momentum A-share Shenwan industry strategy backtest / industry rotation PE PB valuation A-share quant strategy / sector momentum rotation A-share multi-factor backtest

### 8. margin_signal (融资融券)

- CN: A股 融资融券 融资余额 融券余额 量化信号 策略 回测 / A股 融资净买入 融券余量 杠杆资金 策略 回测 / A股 两融数据 融资买入 预测收益 策略 回测
- EN: margin trading balance融资余额 A-share signal strategy backtest / short selling balance margin data A-share quant strategy / leverage flow融资净买入 A-share stock selection backtest

### 9. northbound_flow (北向资金)

- CN: A股 北向资金 沪股通 深股通 量化策略 回测 / A股 港资持股 北向资金流 选股策略 回测 / A股 外资流入 沪深港通 持股比例 策略 回测
- EN: northbound capital flow沪股通 A-share strategy backtest / Hong Kong connect holding ratio A-share quant strategy / foreign inflow Stock Connect A-share stock selection backtest

### 10. multi_factor (多因子合成)

- CN: A股 多因子合成 价值 成长 质量 资金流 量化策略 回测 / A股 因子投资 Barra 风险模型 多因子 策略 回测 / A股 因子选股 PE PB ROE 换手率 资金流 多因子 策略 / 华泰 金工 多因子合成 研报 策略 回测
- EN: multi-factor composite value quality momentum A-share strategy backtest / factor investing Barra risk model A-share quant strategy / multi-factor stock selection A-share PE PB ROE turnover backtest

### 11. analyst_signal (分析师信号)

- CN: A股 分析师评级 买入卖出 净买入 量化信号 策略 回测 / A股 机构调研 净买入 调研信号 选股策略 回测 / A股 分析师一致预期 EPS 目标价 量化策略 回测
- EN: analyst rating buy sell net buy A-share signal strategy backtest / institutional research visit net buy A-share quant strategy / analyst consensus EPS target price A-share backtest

### 12. macro_rate (宏观利率)

- CN: A股 宏观因子 CPI PMI M2 利率 策略 回测 / A股 Shibor 利率因子 国债逆回购 大类资产配置 策略 / A股 宏观择时 GDP PMI 流动性 量化策略 回测
- EN: macro factor CPI PMI M2 interest rate A-share timing strategy backtest / Shibor rate factor liquidity A-share allocation strategy / GDP PMI monetary policy A-share quantitative timing backtest

## 搜索机制

每个关键字与site:限制组合后，还会追加 `quantitative strategy backtest` 后缀，通过SearXNG (localhost:11236) + crawl4ai (localhost:11235) 执行搜索和抓取。华泰/中信研报类关键字不使用site:限制，而是通过来源前缀标记来归类。
