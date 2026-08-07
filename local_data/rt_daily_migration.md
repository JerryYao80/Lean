# A-Share ETF T0 Live Paper - Daily Data Migration

## 概述

将 live-paper 数据源从 `rt_min`（分钟线）迁移到 `rt_etf_k`（ETF日线）和 `rt_k`（个股日线）。

## 修改内容

### 1. 新增模块：`rt_daily_downloader.py`

创建了新的日线数据下载器，支持：
- **ETF 日线**：使用 Tushare `rt_etf_k` API
- **个股日线**：使用 Tushare `rt_k` API
- **自动识别**：根据 ts_code 自动选择合适的 API
- **友好输出**：实时显示下载进度和关键指标

#### 主要类

```python
class TushareRtDailyClient:
    def __init__(self, token, batch_size=25, http_url=None)
    def fetch_quotes(self, ts_codes: Sequence[str]) -> pd.DataFrame
```

#### 输出示例

```
📊 Fetching daily data for 10 symbols...
  [1/10] 510300.SH... 📈 ¥4.523 (+1.23%)
  [2/10] 510500.SH... 📉 ¥6.789 (-0.45%)
  ...
✅ Successfully fetched 10 records
```

### 2. 修改：`ashare_etf_t0_feature_live_bridge.py`

#### 移除的功能
- ❌ `rt_min_downloader` 导入
- ❌ `TushareRtMinClient` 使用
- ❌ `live-feature-minute-frequency` 配置参数
- ❌ `--minute-frequency` 命令行参数

#### 新增的功能
- ✅ `rt_daily_downloader` 导入
- ✅ `TushareRtDailyClient` 使用
- ✅ 增强的终端输出显示
- ✅ 实时进度跟踪

#### 输出增强

**刷新周期显示：**
```
================================================================================
🔄 Refreshing live features at 2026-03-11 14:30:00
📅 Session: 20260311 | Universe: 50 symbols
================================================================================

📊 Fetching daily data for 50 symbols...
  [1/50] 510300.SH... 📈 ¥4.523 (+1.23%)
  ...

📝 Processing 50 quotes...
────────────────────────────────────────────────────────────────────────────────
  [1/50] 510300.SH    ✅ ¥   4.52 📈 +1.23% | M5: +2.45% | Vol:  1.23
  [2/50] 510500.SH    ✅ ¥   6.79 📉 -0.45% | M5: -0.89% | Vol:  0.98
  ...
────────────────────────────────────────────────────────────────────────────────
✅ Written: 48 | ⏭️  Skipped: 2
================================================================================

⏳ Sleeping 30s until next poll (iteration 1)...
```

### 3. 配置变更

#### 移除的配置项
```json
{
  "live-feature-minute-frequency": "1MIN"  // ❌ 不再需要
}
```

#### 保留的配置项
```json
{
  "live-feature-poll-interval-seconds": 30,
  "live-feature-quote-batch-size": 25,
  "live-feature-bootstrap-history": true
}
```

### 4. 测试脚本：`test_rt_daily_downloader.py`

用于验证新的日线下载器功能。

#### 使用方法

```bash
cd /home/project/hope/Lean/Scripts
python test_rt_daily_downloader.py --token YOUR_TUSHARE_TOKEN
```

#### 测试内容
1. ETF 日线数据（`rt_etf_k`）
2. 个股日线数据（`rt_k`）
3. 混合数据（ETF + 个股）

## API 参考

### rt_etf_k（ETF日线）
- **文档**：https://tushare.pro/document/2?doc_id=400
- **接口**：`pro.rt_etf_k(ts_code='510300.SH')`
- **返回字段**：ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount

### rt_k（个股日线）
- **文档**：https://tushare.pro/document/2?doc_id=372
- **接口**：`pro.rt_k(ts_code='000001.SZ')`
- **返回字段**：ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount

## 运行方式

### 测试模式（单次运行）
```bash
cd /home/project/hope/Lean
python Scripts/ashare_etf_t0_feature_live_paper.py --bridge-only --config Launcher/config/config-ashare-etf-t0-feature-live-paper.json
```

### 生产模式（持续运行）
```bash
cd /home/project/hope/Lean
python Scripts/ashare_etf_t0_feature_live_paper.py --config Launcher/config/config-ashare-etf-t0-feature-live-paper.json
```

## 优势

1. **更稳定**：日线 API 比分钟线 API 更稳定，数据质量更高
2. **更高效**：单次请求获取完整日线数据，无需聚合分钟线
3. **更清晰**：实时终端输出，避免黑箱操作
4. **更简洁**：移除了不必要的分钟频率配置

## 注意事项

1. **API 权限**：确保 Tushare token 有 `rt_etf_k` 和 `rt_k` 接口权限
2. **数据延迟**：实时日线数据可能有几秒到几分钟的延迟
3. **交易时段**：默认仅在交易时段（9:30-11:30, 13:00-15:00）更新，可用 `--run-outside-market-hours` 强制运行

## 兼容性

- ✅ 与现有 feature 计算逻辑完全兼容
- ✅ 输出格式与原 `rt_min` 聚合结果一致
- ✅ 无需修改 LEAN 算法代码
