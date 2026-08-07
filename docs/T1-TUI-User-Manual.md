# A股 T+1 TUI 使用手册

## 启动方式
### 方式一：仅启动 live-paper 引擎
```bash
python Scripts/ashare_t1_live_paper.py --launcher-only
```

### 方式二：启动引擎并打开 TUI
```bash
python Scripts/ashare_t1_live_paper.py
```

### 方式三：直接打开 TUI
```bash
python Scripts/ashare_t1_tui.py Launcher/config/config-ashare-t1-live-paper.json
```

## 页面说明
- 账户总览：总资产、可用资金、持仓市值、累计收益
- 交易信号：最近 10 条 advisory 信号
- 持仓明细：数量、可卖数量、成本、现价、盈亏
- `0 (T+1)`: 今日买入仓位，当日不可卖

## 数据来源
TUI 从以下文件读取运行状态：
- `signal-file`
- `portfolio-snapshot-file`
- `daily-summary-file`
- `Results/signals/signals_YYYYMMDD.jsonl`

## 常见问题
1. 若无信号文件，先确认 live-paper 已进入 `Running`
2. 若只看到空仓快照，说明当前还未触发策略信号
3. 若直接读取 launcher 配置，TUI 会自动展开 `parameters` 并解析相对路径
