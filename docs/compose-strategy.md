# LEAN 量化策略组合指南

本文档介绍如何在 LEAN 回测引擎中组合多个量化策略，无需修改项目原有代码。

---

## 概述

LEAN 提供了 **Alpha 层面** 的多策略组合框架，开箱即用。主要通过 `CompositeAlphaModel` 实现多个 Alpha 模型的合并。

---

## 方法一：CompositeAlphaModel（推荐）

### 原理

`CompositeAlphaModel` 是 LEAN 内置的 Alpha 组合器，可将多个 Alpha 模型合并为一个统一的 Alpha 模型输出。

- 依次调用每个子 Alpha 模型
- 合并所有产生的 Insights 输出
- 自动设置每个 Insight 的 SourceModel 属性（便于追踪来源）

### 使用方式

在您的算法中直接使用：

```csharp
public class MyCompositeAlgorithm : QCAlgorithm
{
    public override void Initialize()
    {
        SetStartDate(2020, 1, 1);
        SetCash(100000);
        
        AddEquity("SPY");
        AddEquity("IBM");
        
        // 使用 CompositeAlphaModel 组合多个策略
        SetAlpha(new CompositeAlphaModel(
            new RsiAlphaModel(),              // 策略1：RSI 均值回归
            new EmaCrossAlphaModel(),         // 策略2：EMA 金叉死叉
            new MacdAlphaModel()              // 策略3：MACD 动量
        ));
        
        // 组合模型配置
        SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
        SetExecution(new ImmediateExecutionModel());
        SetRiskManagement(new NullRiskManagementModel());
    }
}
```

### Python 示例

```python
from QuantConnect.Algorithm.Framework.Alphas import CompositeAlphaModel
from QuantConnect.Algorithm.Framework.Alphas import RsiAlphaModel, EmaCrossAlphaModel

class MyCompositeAlgorithm(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetCash(100000)
        
        self.AddEquity("SPY")
        
        self.SetAlpha(CompositeAlphaModel(
            RsiAlphaModel(),
            EmaCrossAlphaModel()
        ))
        
        self.SetPortfolioConstruction(EqualWeightingPortfolioConstructionModel())
        self.SetExecution(ImmediateExecutionModel())
```

### 参考实现

- 源代码：`Algorithm/Alphas/CompositeAlphaModel.cs`
- 示例算法：`Algorithm.CSharp/CompositeAlphaModelFrameworkAlgorithm.cs`

---

## 方法二：Portfolio Construction 层面组合

如果在**仓位权重**层面进行策略组合，可以使用不同的 PortfolioConstructionModel：

### 可用模型

| 模型 | 说明 |
|------|------|
| `EqualWeightingPortfolioConstructionModel` | 等权重分配 |
| `MeanVarianceOptimizationPortfolioConstructionModel` | 均值方差优化 (MVO) |
| `RiskParityPortfolioConstructionModel` | 风险平价组合 |
| `BlackLittermanOptimizationPortfolioConstructionModel` | Black-Litterman 模型 |
| `AccumulativeInsightPortfolioConstructionModel` | 累计 Insight 权重 |

### 使用示例

```csharp
// 使用均值方差优化进行组合
SetPortfolioConstruction(new MeanVarianceOptimizationPortfolioConstructionModel());

// 或使用风险平价
SetPortfolioConstruction(new RiskParityPortfolioConstructionModel());
```

这些模型会在**生成持仓权重时**考虑多个 Alpha 的信号，智能分配资金。

---

## 方法三：自定义 Alpha 组合逻辑

如果需要更复杂的组合逻辑（如加权平均、投票、信号过滤等），可以创建自定义 AlphaModel：

```csharp
public class WeightedAlphaModel : IAlphaModel
{
    private readonly Dictionary<string, double> _weights;
    private readonly IAlphaModel _model1;
    private readonly IAlphaModel _model2;
    
    public WeightedAlphaModel(IAlphaModel model1, IAlphaModel model2, 
                              double weight1 = 0.5, double weight2 = 0.5)
    {
        _model1 = model1;
        _model2 = model2;
        _weights = new Dictionary<string, double>
        {
            { "model1", weight1 },
            { "model2", weight2 }
        };
    }
    
    public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
    {
        // 实现自定义组合逻辑
        var insights1 = _model1.Update(algorithm, data);
        var insights2 = _model2.Update(algorithm, data);
        
        // 合并并加权处理
        // ...
    }
}
```

---

## 配置建议

### 策略数量

- 建议组合 **2-5 个** 策略
- 过多策略会增加复杂度且难以调试
- 确保策略之间具有一定独立性

### 策略类型搭配

建议组合不同类型的策略以分散风险：

- **趋势跟踪** + **均值回归**
- **动量** + **价值**
- **短期** + **中长期**

### 权重分配

- 等权重：简单高效
- 动态权重：根据策略表现调整
- 风险加权：基于策略波动率分配

---

## 运行回测

1. 创建新算法类，继承 `QCAlgorithm`
2. 在 `Initialize()` 中使用 `SetAlpha(new CompositeAlphaModel(...))`
3. 配置数据源和回测参数
4. 运行回测

```bash
dotnet run --project Launcher --algorithm-type-name MyCompositeAlgorithm
```

---

## 总结

| 方法 | 适用场景 | 复杂度 |
|------|----------|--------|
| CompositeAlphaModel | 多 Alpha 信号组合 | 低 |
| PortfolioConstructionModel | 资金权重优化 | 中 |
| 自定义 AlphaModel | 特殊组合逻辑 | 高 |

推荐从 `CompositeAlphaModel` 开始，它是 LEAN 原生支持的方式，无需修改项目代码即可快速实现多策略组合回测。
