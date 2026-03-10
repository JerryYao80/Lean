# 修复总结: 解决2747个失败的下载

## 问题分析

在执行 `download_all.py` 时，10267条数据中有2747条失败，涉及14个API。

### 失败原因

所有失败都是因为**参数校验错误**。这些API不接受 `start_date` 和 `end_date` 参数，而是需要:
- 单个日期参数（如 `trade_date`）
- 或者股票/基金代码（`ts_code`）

## 修复方案

### 1. 更新API配置 (api_registry.py)

**DATE策略API（11个）- 需要单个trade_date而非日期范围:**
- weekly, monthly (股票周线/月线)
- index_weekly, index_monthly, index_dailybasic, index_weight (指数)
- fut_holding, fut_wsr, fut_settle (期货)

**STOCK策略API（3个）- 需要按股票代码迭代:**
- forecast, dividend (业绩预告、分红)
- fund_daily, fund_div, fund_nav (基金数据)

### 2. 更新下载器 (downloader.py)

**修改的方法:**
1. `download_api_by_date()` - 修改为使用单个日期参数，不再使用start_date/end_date
2. `download_api_by_stock()` - **新增**按股票代码下载的方法
3. `_get_stock_list()` - **新增**获取股票/基金列表的辅助方法
4. `_create_download_tasks()` - 添加对STOCK策略的支持
5. `_execute_task()` - 添加STOCK策略的执行分支

## 验证结果

所有14个失败的API配置已成功更新:

| API | 新策略 | 日期字段 | 代码字段 |
|-----|--------|---------|---------|
| weekly | DATE | trade_date | - |
| monthly | DATE | trade_date | - |
| forecast | STOCK | ann_date | ts_code |
| dividend | STOCK | - | ts_code |
| fund_daily | STOCK | trade_date | ts_code |
| fund_div | STOCK | - | ts_code |
| fund_nav | STOCK | nav_date | ts_code |
| index_dailybasic | DATE | trade_date | - |
| index_weekly | DATE | trade_date | - |
| index_monthly | DATE | trade_date | - |
| index_weight | DATE | trade_date | - |
| fut_holding | DATE | trade_date | - |
| fut_wsr | DATE | trade_date | - |
| fut_settle | DATE | trade_date | - |

## 使用建议

1. **不会重复下载**: 现有的断点续传逻辑会自动跳过已下载的数据
2. **下次运行**: 直接执行 `python download_all.py` 即可，失败的2747个任务会被正确处理
3. **监控**: 检查 logs/tushare_err.log 确认不再有"参数校验失败"错误

## 技术细节

### DATE策略变更
**之前:** `start_date=20200101&end_date=20201231`
**之后:** `trade_date=20200101` (遍历每个日期)

### STOCK策略实现
1. 从 `stock_basic` 或 `fund_basic` 获取所有代码列表
2. 对每个 ts_code 发起单独请求: `ts_code=000001.SZ`
3. 保存到独立文件避免冲突

### 性能考虑
- DATE策略: 每年约250个交易日 × 5年 = 1250个任务/API
- STOCK策略: 约5000只股票 × 1 = 5000个任务/API
- 使用并发下载和断点续传优化性能
