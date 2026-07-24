建议顺序：**先 Layer C 上 live-paper，200 trials 和策略接入可以并行/滞后。**

理由:

- **200 trials 是低风险扩展**——闭环已验证，无非是算力和等待时间,可以后台跑,不占用你的决策带宽。
- **Layer C 是唯一还没有在准实盘条件下验证过的环节**——ONNX policy 训练时的分布和 live 推理时的分布必然有偏移,ZeroMQ IPC 的延迟、C# 端 RlRiskModel 收到异常/超时后的降级行为,这些只有跑起来才能暴露。越早发现问题,后面接入更多策略时踩的坑越少。
- **策略接入应该排最后**——在核心优化器+风控闭环还没有经过真实 trials 数量和真实 live-paper 周期检验之前,扩大策略数量只是把未验证的风险面复制了几份。

Layer C 上线时建议重点检查:

1. **kill-switch 是结构性的**——ZeroMQ 连接断开/超时时 RlRiskModel 要有明确的 fail-safe 默认行为(比如退化到固定风控规则),而不是依赖 C# 端逻辑判断,这个应该在契约层面强制。
2. **ONNX 推理确定性**——训练时的 PPO policy 和导出后的 ONNX 在相同输入下是否严格一致(数值精度、动作分布采样方式),建议先做一次 offline replay 对比。
3. **延迟预算**——round-trip (C# 请求 → ZeroMQ → Python inference → 返回) 在真实 tick 频率下是否会造成决策滞后,尤其考虑 A 股涨跌停/T+1 的时间敏感性。

200 trials 那边只需要留意 DSR 的 trial 惩罚项在样本量变大后是否还合理(避免过度惩罚导致陷入局部最优),以及 CPCV/walk-forward 的 OOS 衰减曲线。

需要我帮你梳理 live-paper 阶段的监控指标清单或者 kill-switch 的具体状态机设计吗?
