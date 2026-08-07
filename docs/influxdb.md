# LEAN 集成 InfluxDB 指南

本文档介绍如何在 LEAN 量化交易引擎中集成 InfluxDB 时序数据库。

---

## 1. InfluxDB 简介

### 什么是 InfluxDB？

InfluxDB 是一个开源的时序数据库（Time Series Database），专为以下场景设计：
- 监控指标存储
- 实时数据分析
- 物联网数据
- **金融交易数据**

### 为什么选择 InfluxDB？

| 特性 | 优势 |
|------|------|
| 时序优化 | 高效存储和查询时间序列数据 |
| 高写入性能 | 支持百万级数据点/秒写入 |
| 内置压缩 | 节省存储空间 |
| HTTP API | 易于集成 |
| Flux 查询语言 | 强大的时序分析能力 |

---

## 2. 在 LEAN 中的使用场景

### 2.1 存储内容

| 数据类型 | 描述 | 写入频率 |
|----------|------|----------|
| **Equity** | 账户权益 | 每分钟/每秒 |
| **Holdings** | 持仓数据 | 变化时 |
| **Orders** | 订单记录 | 下单时 |
| **Trades** | 成交记录 | 成交时 |
| **Performance** | 绩效指标 | 每日 |
| **Logs** | 运行日志 | 实时 |

### 2.2 适用场景

- **实时监控**：Live Trading 时实时写入 InfluxDB
- **历史分析**：查询历史回测/实盘数据
- **可视化**：对接 Grafana 实现高级可视化
- **异常检测**：基于时序数据的异常告警
- **策略回测**：存储和分析大规模回测结果

---

## 3. 集成方案

### 方案一：自定义 ResultHandler

创建一个自定义的 `InfluxDbResultHandler`，继承自 `BaseResultsHandler`：

```csharp
// InfluxDbResultHandler.cs
public class InfluxDbResultHandler : BaseResultsHandler, IResultHandler
{
    private readonly InfluxDBClient _client;
    private readonly string _bucket;
    private readonly string _org;
    
    public InfluxDbResultHandler(string url, string token, string org, string bucket)
    {
        _client = InfluxDBClientFactory.Create(url, token);
        _org = org;
        _bucket = bucket;
    }
    
    // 实现接口方法，将数据写入 InfluxDB
    public override void Sample(DateTime time)
    {
        // 写入权益数据点
        var point = PointData.Measurement("equity")
            .Tag("algorithm", AlgorithmId)
            .Field("value", Algorithm.Portfolio.TotalPortfolioValue)
            .Timestamp(time);
        
        _client.WriteApi.WritePoint(point, _bucket, _org);
    }
}
```

### 方案二：作为附加存储

在现有的 ResultHandler 基础上，增加 InfluxDB 写入：

```csharp
// 装饰器模式
public class InfluxDbResultHandlerWrapper : IResultHandler
{
    private readonly IResultHandler _inner;
    private readonly InfluxDBClient _influxClient;
    
    public InfluxDbResultHandlerWrapper(IResultHandler inner, InfluxDBClient influxClient)
    {
        _inner = inner;
        _influxClient = influxClient;
    }
    
    public void Sample(DateTime time)
    {
        // 原有逻辑
        _inner.Sample(time);
        
        // 额外写入 InfluxDB
        WriteToInfluxDB(time);
    }
    
    private void WriteToInfluxDB(DateTime time)
    {
        // 写入各类数据
    }
}
```

### 方案三：算法内部直接写入

在策略中直接使用 InfluxDB：

```csharp
public class MyAlgorithm : QCAlgorithm
{
    private InfluxDBClient _influxClient;
    private string _bucket = "lean-trading";
    private string _org = "my-org";
    
    public override void Initialize()
    {
        // 初始化 InfluxDB 客户端
        _influxClient = InfluxDBClientFactory.Create(
            "http://localhost:8086", 
            "my-token"
        );
        
        // 定时采样
        Schedule.On(
            DateRules.EveryDay(), 
            TimeRules.Every(TimeSpan.FromMinutes(1)),
            SamplePortfolio
        );
    }
    
    private void SamplePortfolio()
    {
        var point = PointData.Measurement("portfolio")
            .Tag("symbol", "portfolio")
            .Field("equity", Portfolio.TotalPortfolioValue)
            .Field("cash", Portfolio.Cash)
            .Field("holdings", Portfolio.TotalHoldingsValue)
            .Timestamp(DateTime.UtcNow);
            
        _influxClient.WriteApi.WritePoint(point, _bucket, _org);
    }
}
```

---

## 4. 配置步骤

### 4.1 添加依赖

在 `Engine/QuantConnect.Lean.Engine.csproj` 中添加：

```xml
<PackageReference Include="InfluxDB.Client" Version="4.8.0" />
```

### 4.2 配置文件

在 `config.json` 中添加：

```json
{
  "influxdb": {
    "url": "http://localhost:8086",
    "token": "your-token",
    "org": "your-org",
    "bucket": "lean-trading",
    "enabled": true
  },
  "result-handler": "InfluxDbResultHandler"
}
```

### 4.3 启动 InfluxDB

```bash
# 使用 Docker 启动
docker run -d \
  --name influxdb \
  -p 8086:8086 \
  -v influxdb2:/var/lib/influxdb22 \
  -e DOCKER_INFLUXDB_INIT_MODE=setup \
  -e DOCKER_INFLUXDB_INIT_USERNAME=admin \
  -e DOCKER_INFLUXDB_INIT_PASSWORD=password123 \
  -e DOCKER_INFLUXDB_INIT_ORG=my-org \
  -e DOCKER_INFLUXDB_INIT_BUCKET=lean-trading \
  -e DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=my-token \
  influxdb:latest
```

---

## 5. 数据模型设计

### 5.1 Measurement 设计

#### equity (权益)

```
measurement: equity
tags: algorithm_id, strategy_name
fields: value (float)
timestamp: UTC time
```

#### holdings (持仓)

```
measurement: holdings
tags: symbol, algorithm_id
fields: quantity, average_price, market_value, unrealized_pnl
timestamp: UTC time
```

#### orders (订单)

```
measurement: orders
tags: symbol, algorithm_id, status, direction
fields: quantity, price, filled_quantity, fee
timestamp: UTC time
```

#### trades (成交)

```
measurement: trades
tags: symbol, algorithm_id, order_id
fields: quantity, price, direction, fees
timestamp: UTC time
```

### 5.2 Bucket 设计

| Bucket | 保留策略 | 用途 |
|--------|----------|------|
| lean-live | 30天 | 实盘数据 |
| lean-backtest | 永久 | 回测数据 |
| lean-archive | 1年 | 历史归档 |

---

## 6. 查询示例

### 6.1 查询权益曲线

```flux
from(bucket: "lean-trading")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "equity")
  |> filter(fn: (r) => r.algorithm_id == "my-strategy")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
```

### 6.2 查询持仓历史

```flux
from(bucket: "lean-trading")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "holdings")
  |> filter(fn: (r) => r.symbol == "SPY")
  |> last()
```

### 6.3 计算绩效指标

```flux
from(bucket: "lean-trading")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "equity")
  |> difference()
  |> mean()
```

---

## 7. Grafana 可视化

### 7.1 配置 Grafana 数据源

1. 打开 Grafana (http://localhost:3000)
2. 添加数据源 → InfluxDB
3. 填写 URL 和 Token
4. 选择 Bucket

### 7.2 常用图表

| 图表类型 | 查询 |
|----------|------|
| 权益曲线 | SELECT mean(value) FROM equity WHERE $timeFilter GROUP BY time($__interval) |
| 回撤图 | FROM(equity) → cumulativeSum → -1 |
| 持仓饼图 | SELECT last(quantity) FROM holdings GROUP BY symbol |
| 交易分布 | SELECT count() FROM trades GROUP BY direction |

### 7.3 Dashboard 示例

```json
{
  "panels": [
    {
      "title": "权益曲线",
      "type": "timeseries",
      "targets": [{
        "query": "SELECT mean(value) FROM equity WHERE algorithm_id='$algo' GROUP BY time($__interval)"
      }]
    },
    {
      "title": "持仓分布",
      "type": "piechart",
      "targets": [{
        "query": "SELECT last(market_value) FROM holdings GROUP BY symbol"
      }]
    },
    {
      "title": "绩效指标",
      "type": "stat",
      "targets": [{
        "query": "SELECT last(value) FROM equity"
      }]
    }
  ]
}
```

---

## 8. 性能优化

### 8.1 写入优化

```csharp
// 批量写入
var points = new List<PointData>();
foreach (var holding in Portfolio)
{
    points.Add(PointData.Measurement("holdings")
        .Tag("symbol", holding.Key)
        .Field("value", holding.Value.TotalMarketValue)
        .Timestamp(DateTime.UtcNow));
}

// 批量提交
_writeApi.WritePoints(points, bucket, org);
```

### 8.2 写入配置

```csharp
var options = new WriteOptions()
{
    BatchSize = 5000,
    FlushInterval = 1000,  // 1秒
    RetryInterval = 5000
};
```

### 8.3 建议配置

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| BatchSize | 5000 | 每批写入点数 |
| FlushInterval | 1000ms | 刷新间隔 |
| RetryInterval | 5000ms | 重试间隔 |
| JitterInterval | 100ms | 抖动防止冲突 |

---

## 9. 完整示例

### 9.1 自定义 ResultHandler

```csharp
using InfluxDB.Client;
using InfluxDB.Client.Api.Domain;
using InfluxDB.Client.Writes;
using QuantConnect.Lean.Engine.Results;
using QuantConnect.Interfaces;
using QuantConnect.Orders;

public class InfluxDbResultHandler : BaseResultsHandler, IResultHandler
{
    private readonly InfluxDBClient _client;
    private readonly string _bucket;
    private readonly string _org;
    private readonly WriteApiAsync _writeApi;
    
    public InfluxDbResultHandler(string url, string token, string org, string bucket)
    {
        _client = InfluxDBClientFactory.Create(url, token);
        _org = org;
        _bucket = bucket;
        _writeApi = _client.GetWriteApiAsync();
    }
    
    public override void Sample(DateTime time)
    {
        base.Sample(time);
        
        if (Algorithm?.Portfolio == null) return;
        
        // 写入权益
        var equityPoint = PointData.Measurement("equity")
            .Tag("algorithm", Algorithm.Name)
            .Field("value", (double)Algorithm.Portfolio.TotalPortfolioValue)
            .Field("cash", (double)Algorithm.Portfolio.Cash)
            .Field("holdings", (double)Algorithm.Portfolio.TotalHoldingsValue)
            .Timestamp(time, WritePrecision.Ns);
            
        _writeApi.WritePoint(equityPoint, _bucket, _org);
    }
    
    public override void OrderEvent(OrderEvent newEvent)
    {
        base.OrderEvent(newEvent);
        
        // 写入订单事件
        var orderPoint = PointData.Measurement("orders")
            .Tag("algorithm", Algorithm?.Name ?? "")
            .Tag("symbol", newEvent.Symbol.Value)
            .Tag("status", newEvent.Status.ToString())
            .Field("quantity", (double)newEvent.Quantity)
            .Field("fillPrice", (double)newEvent.FillPrice)
            .Field("direction", newEvent.Direction.ToString())
            .Timestamp(newEvent.Time, WritePrecision.Ns);
            
        _writeApi.WritePoint(orderPoint, _bucket, _org);
    }
}
```

### 9.2 配置使用

```json
{
  "result-handler": "MyNamespace.InfluxDbResultHandler",
  "influxdb-url": "http://localhost:8086",
  "influxdb-token": "my-token",
  "influxdb-org": "my-org",
  "influxdb-bucket": "lean-trading"
}
```

---

## 10. 注意事项

1. **版本兼容**：使用 InfluxDB 2.x 客户端库
2. **时间精度**：建议使用纳秒精度 (WritePrecision.Ns)
3. **数据清理**：配置合理的保留策略
4. **网络安全**：生产环境使用 HTTPS
5. **错误处理**：实现重试和降级逻辑

---

## 11. 总结

| 集成方式 | 复杂度 | 适用场景 |
|----------|--------|----------|
| 算法内直接写入 | 低 | 单策略简单监控 |
| 自定义 ResultHandler | 中 | 多策略统一管理 |
| ResultHandler 包装器 | 中 | 保留原有功能 |

InfluxDB 集成可实现：
- ✅ 实时监控和可视化
- ✅ 历史数据查询分析
- ✅ 对接 Grafana 丰富图表
- ✅ 高性能时序数据存储
