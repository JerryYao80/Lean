# 爬取可视化修改验证

## 手动部署 Dashboard（一条命令）

```bash
cp monitoring/grafana/dashboards/lean/soloquant-crawl-visibility.json /home/project/curiocity/Lean/grafana/dashboards/soloquant-crawl-visibility.json
```

Grafana 每 10 秒自动扫描 dashboards 目录，复制后 dashboard 会自动出现。

---

## 验证成果：两个入口

### 1. Grafana 查看（推荐）

打开浏览器访问：

```
http://84.8.248.198:3000 → Dashboards → "SoloQuant 爬取可视化"
```

你应该看到 11 个面板：

| Row | 面板 | 验证内容 |
|-----|------|----------|
| 1 | 今日爬取条目总数 | 有数值（来自 `soloquant_crawl_source`） |
| 1 | 今日重磅条目数 (≥7分) | 有数值（来自 `soloquant_crawl_item`，测试数据有 2 条 ≥7） |
| 1 | 平均及时性 | gauge 指针（来自 `soloquant_crawl_source`） |
| 1 | 平均重磅评分 | gauge 指针 |
| 2 | 各数据源爬取量时序 | stacked area 图（按 source_name 分色） |
| 3 | 及时性 vs 重磅性散点图 | X=lag_minutes, Y=weight_score |
| 4 | 各数据源质量分数趋势 | 线图 = 及时性 × 重磅 / 10 |
| 5 | 数据源健康状态 | 表格（source_name, status, crawled, avg_weight） |
| 6 | Local-Src 新增文件 | 按重量等级分色（来自 `soloquant_local_src`） |
| 6 | Local-Src 最近处理文件 | 表格 |
| 7 | 重磅条目时间线 (≥7分) | 时间轴（显示 Fed_RSS 9.0、arXiv_API 8.5） |

### 2. Prefect UI 查看

```
http://84.8.248.198:4200 → Deployments → soloquant-pipeline-scheduled
```

点击最近的 Flow Run，展开 Task 列表，你应该看到两个新 task：
- `watch-local-src` — 扫描 local-src 目录
- `crawl-api-sources` — 调用 arXiv/Fed/FRED API

### 3. 已验证的结果

| 验证项 | 结果 |
|--------|------|
| local-src watcher 发现新文件 | ✅ `new_files: 1`, `score: 3.5`, `lag: 22.6min` |
| watcher state 持久化 | ✅ `local-src-state.json` 已写入 |
| arXiv API 爬取 | ✅ 返回 20 条论文 |
| InfluxDB measurements 存在 | ✅ `soloquant_crawl_source` + `soloquant_crawl_item` + `soloquant_local_src` |
| 测试数据写入 InfluxDB | ✅ 4 条测试点（2条≥7分重磅 + 1条medium + 1条local-src） |
| 规则评分逻辑 | ✅ 降息=3.5, 央行=3.5, 无信号=2.0, 策略=2.5 |
