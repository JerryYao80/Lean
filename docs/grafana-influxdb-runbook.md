# LEAN Grafana/InfluxDB Runbook

本仓库已经把 LEAN 回测与 `live-paper` 的结果流并行写入 InfluxDB。

## 1. 默认运行参数

- InfluxDB URL: `http://127.0.0.1:8086`
- InfluxDB Org: `lean`
- InfluxDB Bucket: `quant`
- InfluxDB Token Env: `INFLUXDB_TOKEN`
- Grafana 容器内访问 InfluxDB: `http://lean-influxdb:8086`

你当前环境对应的 token 是：

```bash
export INFLUXDB_TOKEN=admin-token-leansystem
```

## 2. LEAN 侧已经导出的 measurement

- `lean_portfolio`
- `lean_holding`
- `lean_cash`
- `lean_order`
- `lean_order_event`
- `lean_trade`
- `lean_chart`
- `lean_metric`
- `lean_message`

## 3. 运行回测或 live-paper

只要使用 `Launcher/config.json` 或本仓库内对应的 LEAN 配置文件启动，结果处理器就会自动尝试写入 InfluxDB。

示例：

```bash
export INFLUXDB_TOKEN=admin-token-leansystem
/usr/local/dotnet/dotnet build Engine/QuantConnect.Lean.Engine.csproj -c Debug --no-restore
./QuantConnect.Lean.Launcher --config Launcher/config/config-barra-cne5-backtest.json
./QuantConnect.Lean.Launcher --config Launcher/config/config-barra-cne5-live-paper.json
```

## 4. 安装 Grafana 资产到运行中的容器

仓库内已经准备好 provisioning 和 dashboard 文件。直接执行：

```bash
export INFLUXDB_TOKEN=admin-token-leansystem
bash monitoring/grafana/install_assets.sh
```

默认会写入运行中的 `lean-grafana` 容器并重启它。

## 5. 配置公网访问地址

如果你是通过公网 IP 直接访问这台机器，例如 `84.8.248.198:3000`，可以执行：

```bash
bash monitoring/grafana/configure_public_access.sh
```

可选环境变量：

```bash
PUBLIC_HOST=84.8.248.198
PUBLIC_PORT=3000
PUBLIC_PROTOCOL=http
PUBLIC_URL=http://84.8.248.198:3000
```

脚本会把 Grafana 的 `domain` 和 `root_url` 写到运行中的 `lean-grafana` 容器并重启它。

## 6. Grafana 面板内容

- 组合净值、现金、持仓市值
- 浮盈浮亏、净利润、费用、剩余保证金
- 当前持仓快照
- 订单事件表
- 已平仓交易表
- 运行时指标快照
- 任意 LEAN 图表序列浏览
- 日志、状态、运行错误消息流
