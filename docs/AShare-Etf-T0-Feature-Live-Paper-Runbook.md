# AShare ETF T0 Feature Live-Paper 启动说明

## 目标

这条链路用于把 **Tushare 实时 ETF 行情** 转成 `AShareEtfT0FeatureIntradayAlgorithm` 可消费的本地特征 CSV，再由 LEAN 以 `live-paper` 模式运行策略。

现有旧配置 `Launcher/config/config-ashare-etf-live-paper.json` 保持不变，仍然服务旧的 `ETFMomentumStrategy`；本次新增的是一条**隔离的新 live-paper 链路**，避免影响现有 T+0 ETF 回测与 live-paper。

## 当前打通后的流程

```text
Tushare rt_min (1MIN)
  -> Scripts/ashare_etf_t0_feature_live_bridge.py
  -> Data/alternative/ashare-etf-t0-live-features/<market>/daily/<ticker>.csv
     - 保留历史特征行
     - 追加/覆盖当日 live 特征行
     - 新增 feature_timestamp 列
  -> Algorithm.CSharp/AShareEtfT0FeatureData.cs
     - live 模式读取 feature_timestamp
     - 用 Time/EndTime 表示当次快照时间
  -> AShareEtfT0FeatureIntradayAlgorithm
     - 读取当日最新 feature snapshot
     - 生成 synthetic live-paper 交易计划与日报
```

## 关键实现点

- 实时桥接脚本直接调用 Tushare `rt_min` 1分钟接口，并聚合为当日特征快照，不修改 `/home/project/tushare-downloader/`。
- 策略 live 配置使用新文件 `Launcher/config/config-ashare-etf-t0-feature-live-paper.json`。
- live 特征目录使用新路径 `Data/alternative/ashare-etf-t0-live-features`，不覆盖原有离线导出目录。
- 自定义数据新增 `feature_timestamp` 列，解决同一交易日多次刷新时 LEAN 只能接收第一条的问题。
- 当日 `signal_*` 字段沿用上一交易日的特征值，和离线导出逻辑保持一致。

## 启动前准备

1. 保证本地历史数据已存在：
   - `/home/project/tushare-downloader/tushare_data`
2. 保证 LEAN 已编译：
   - `Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll`
   - `Launcher/bin/Debug/QuantConnect.Lean.Launcher`
3. 保证 Python 环境可用并安装：
   - `pandas`
   - `pyarrow`
   - `tushare`
4. 在当前 shell 中设置 Tushare Token：

```bash
export TUSHARE_TOKEN='你的_tushare_token'
```

## 推荐启动方式

一条命令同时启动 **实时特征桥接** + **LEAN live-paper**：

```bash
python3 Scripts/ashare_etf_t0_feature_live_paper.py
```

## 常用启动方式

仅启动实时特征桥接：

```bash
python3 Scripts/ashare_etf_t0_feature_live_paper.py --bridge-only
```

仅启动 LEAN live-paper：

```bash
python3 Scripts/ashare_etf_t0_feature_live_paper.py --launcher-only
```

手工分别启动：

```bash
python3 Scripts/ashare_etf_t0_feature_live_bridge.py --config Launcher/config/config-ashare-etf-t0-feature-live-paper.json
./Launcher/bin/Debug/QuantConnect.Lean.Launcher --config Launcher/config/config-ashare-etf-t0-feature-live-paper.json
```

## 关键输出

- live 特征桥接报告：`Results/ashare-etf-t0-feature-live-bridge-report.json`
- 策略成交报告：`Results/ashare-etf-t0-feature-live-trades.csv`
- 策略日报：`Results/ashare-etf-t0-feature-live-daily-summary.csv`
- 策略配置：`Launcher/config/config-ashare-etf-t0-feature-live-paper.json`

## 注意事项

- 旧的 `config-ashare-etf-live-paper.json` 没有改动。
- 这条新链路默认只在中国 A 股交易时段轮询；非交易时段桥接脚本会写入 `paused_outside_market_hours` 状态。
- `AShareEtfT0FeatureIntradayAlgorithm` 仍按“每个交易日处理一次”生成当日交易计划；`feature_timestamp` 的作用是保证 live 模式能正确接收最新快照，而不是把策略改成同日多次重复下单。
