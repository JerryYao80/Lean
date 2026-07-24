# A股 T+1 策略开发指南

## 开发流程
1. 使用 `Scripts/ashare_t1_backtest.py` 做单次回测
2. 使用 `Scripts/ashare_t1_optimize.py` 做参数搜索
3. 使用 `Scripts/ashare_t1_report.py` 从 `summary-file` 生成报告
4. 使用 `Scripts/ashare_t1_live_paper.py` 或 `Scripts/ashare_t1_tui.py` 观察 live-paper 状态

## 回测配置
核心字段位于 `Launcher/config/config-ashare-t1-backtest.json`
- `universe`: 静态股票池
- `lookback-period`: zscore 窗口
- `entry-threshold`: 开仓阈值
- `exit-threshold`: 平仓阈值
- `max-positions`: 最大持仓数
- `position-size`: 单标的目标仓位
- `summary-file`: 回测摘要 JSON

## 推荐命令
```bash
python Scripts/ashare_t1_backtest.py --config Launcher/config/config-ashare-t1-backtest.json
python Scripts/ashare_t1_optimize.py --config Launcher/config/config-ashare-t1-optimize.json --param-grid params.json --output Results/ashare-t1-optimize.json
python Scripts/ashare_t1_report.py --backtest-result Results/ashare-t1-summary.json --output Results/reports/
```

## Live-Paper
`AShareT1MeanReversionAlgorithm` 采用 advisory 模式：
- 不向 `PaperBrokerage` 下单
- 输出 `signal-file`
- 输出 `portfolio-snapshot-file`
- 输出 `daily-summary-file`
- 追加 `Results/signals/signals_YYYYMMDD.jsonl`

## 输出文件
- `ashare-t1-trades.csv`: 交易明细
- `ashare-t1-daily.csv`: 日度净值
- `ashare-t1-summary.json`: 结构化摘要
- `ashare-t1-backtest-report.md`: Markdown 报告
