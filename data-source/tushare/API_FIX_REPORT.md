
## 🔍 接口错误检查报告

### ✅ 已发现并修复的问题

#### 1. ggt_top10（港股通十大成交股）
**问题**: 参数校验失败  
**原因**: 接口要求 `ts_code` 或 `trade_date` 参数（二选一），但使用 YEAR 策略只传递了 `start_date` + `end_date`  
**修复方案**: 将策略从 `YEAR` 改为 `DATE`  
**状态**: ✅ 已修复

#### 2. USAGE.md 中的拼写错误
**问题**: `ah_comparision` 拼写错误  
**正确名称**: `stk_ah_comparison`  
**状态**: ✅ 已修复

---

### ⚠️  新发现的问题

#### 3. cyq_perf（每日筹码及胜率）
**问题**: 参数校验失败 - "ts_code,trade_date至少输入一个参数"  
**官方文档**: https://tushare.pro/document/2?doc_id=293  
**输入参数**:
  - ts_code (str, **必填 Y**)
  - trade_date (str, N)
  - start_date (str, N)
  - end_date (str, N)

**官方示例**:
```python
pro.cyq_perf(ts_code='600000.SH', start_date='20220101', end_date='20220429')
```

**当前配置**:
  - 策略: STOCK
  - 代码字段: ts_code
  - 仅传递: ts_code='XXX'

**问题分析**:
虽然官方文档标记 `ts_code` 为必填，但实际API后端要求**至少输入 `ts_code` 或 `trade_date` 之一**。
更重要的是，官方示例显示该接口需要**同时提供 `ts_code` + 日期范围**。

**解决方案**:
该接口不适合单纯的 STOCK 策略（只传 ts_code）。需要：
  1. 改为 DATE 策略，每次传 `trade_date`，或
  2. 自定义处理：传递 `ts_code` + `start_date` + `end_date`

**推荐**: 改为 **DATE 策略**，因为数据量可能很大（按股票+日期双维度查询）

---

### 📝 其他潜在风险接口

需要进一步检查的接口（使用 STOCK 策略但可能需要额外参数）:
- `cyq_chips` - 每日筹码分布（类似 cyq_perf）

