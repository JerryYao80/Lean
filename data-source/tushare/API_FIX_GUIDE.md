# API修复使用指南

## 修复内容总结

### 1. 修复的API名称（22个）

| 原错误名称 | 正确名称 | 说明 |
|-----------|---------|------|
| `cn_yld` | `yc` | 国债收益率曲线 |
| `hk_adj_factor` | `hk_adjfactor` | 港股复权因子 |
| `index_member_sw` | `index_member_all` | 申万行业成分（分级） |
| `ci_member` | `ci_index_member` | 中信行业成分 |
| `tw_elec_income` | `tmt_twincome` | 台湾电子产业月营收 |
| `tw_elec_income_detail` | `tmt_twincomedetail` | 台湾电子产业月营收明细 |
| `hot_money_detail` | `hm_detail` | 游资交易每日明细 |

### 2. 修复的分块策略

#### 财务数据接口（必需ts_code参数）
- `income` (利润表): QUARTER → STOCK
- `balancesheet` (资产负债表): QUARTER → STOCK
- `cashflow` (现金流量表): QUARTER → STOCK
- `fina_indicator` (财务指标): QUARTER → STOCK
- `fina_audit` (财务审计): YEAR → STOCK
- `fina_mainbz` (主营业务构成): QUARTER → STOCK

#### 其他接口
- `yc_cb` (柜台流通式债券): YEAR → DATE (解决2000行限制)
- `index_daily` (指数日线): YEAR → STOCK
- `stk_rewards` (管理层薪酬): YEAR → STOCK
- `top_list` (龙虎榜每日明细): YEAR → DATE
- `top_inst` (龙虎榜机构明细): YEAR → DATE
- `pledge_detail` (股权质押明细): NONE → STOCK
- `cyq_perf` (每日筹码及胜率): YEAR → STOCK
- `cyq_chips` (每日筹码分布): DATE → STOCK
- `bo_monthly/bo_daily/bo_cinema` (票房数据): YEAR → DATE

### 3. 添加的必填参数

- `fut_weekly_monthly`: 添加 `required_params={"freq": "W"}`
- `stk_weekly_monthly`: 添加 `required_params={"freq": "W"}`
- `tmt_twincome`: 添加 `required_params={"item": "1"}`

### 4. 禁用的API（11个不存在的接口）

已注释掉以下API（在tushare文档中不存在）:
- `st_list`
- `hk_hold_list`
- `bj_mapping`
- `stock_basic_history`
- `stk_weekly_monthly_adj`
- `ths_limit_list`
- `limit_ladder`
- `limit_strong_ind`
- `hot_money`
- `kpl_subject`
- `kpl_member`
- `nh_index`
- `fut_limit`
- `film_record`
- `tv_record`

---

## 使用验证脚本

### 基本用法

验证所有启用的API：
```bash
python api_validator.py
```

### 按分类验证

验证特定分类的API：
```bash
python api_validator.py --category stock_basic stock_quote
```

可用的分类：
- `stock_basic` - 股票基础数据
- `stock_quote` - 股票行情数据
- `stock_finance` - 财务数据
- `stock_reference` - 参考数据
- `stock_special` - 特色数据
- `stock_margin` - 两融数据
- `stock_moneyflow` - 资金流向
- `stock_billboard` - 龙虎榜数据
- `stock_concept` - 概念板块
- `index` - 指数数据
- `etf` - ETF数据
- `futures` - 期货数据
- `options` - 期权数据
- `cb` - 可转债数据
- `fx` - 外汇数据
- `hk` - 港股数据
- `us` - 美股数据
- `macro` - 宏观经济
- `industry` - 行业经济

### 自定义输出文件

```bash
python api_validator.py --output my_report.json
```

### 验证报告解读

验证脚本会生成JSON格式的报告，包含：

```json
{
  "timestamp": "2026-01-28T...",
  "summary": {
    "total": 150,
    "success": 120,
    "failed": 5,
    "warnings": 15,
    "skipped": 10
  },
  "details": {
    "success": [...],
    "failed": [...],
    "warnings": [...],
    "skipped": [...]
  }
}
```

#### 状态说明

- **success**: API调用成功，返回了数据
- **warning**: API调用成功但返回空数据（可能测试参数没有对应数据）
- **failed**: API调用失败（名称错误、参数错误等）
- **skipped**: API被禁用或权限不足

---

## 下一步操作

### 1. 运行验证

```bash
cd /home/project/tushare-downloader
python api_validator.py --output validation_report.json
```

### 2. 查看报告

检查 `validation_report.json` 文件，重点关注：
- `failed` 列表：这些API需要进一步修复
- `warnings` 列表：这些API可能需要调整测试参数

### 3. 修复remaining errors

如果验证发现还有错误，根据报告中的 `fix_needed` 字段进行修复：
- `Incorrect API name`: API名称错误
- `Missing required parameters`: 缺少必填参数

### 4. 重新下载数据

验证通过后，清理uniq.log并重新运行下载：

```bash
rm uniq.log
python download_all.py
```

或针对特定分类下载：

```bash
python smart_downloader.py --category stock_finance
```

---

## 常见问题

### Q1: 为什么有些API返回空数据？

A: 可能原因：
1. 测试参数（如日期、股票代码）没有对应的数据
2. API需要更高权限级别
3. 数据确实不存在

这些API标记为 `warning` 状态，通常不需要修复。

### Q2: 如何处理权限不足的API？

A: 这些API会被标记为 `skipped`。如果需要使用，请：
1. 升级tushare积分等级
2. 联系tushare获取权限

### Q3: 修复后还有错误怎么办？

A: 请查看错误信息：
1. 如果是"请指定正确的接口名"，API名称仍然错误
2. 如果是"必填参数"，需要添加对应的required_params
3. 如果是"参数校验失败"，检查chunk_strategy是否合适

---

## 修复效果对比

### 修复前（uniq.log中的错误）
- 170条唯一错误
- 22个API名称错误
- 26个参数配置错误
- 11个不存在的API

### 修复后（预期）
- API名称错误：0个
- 参数配置错误：大幅减少
- 不存在的API：已全部禁用

运行验证脚本以确认修复效果。
