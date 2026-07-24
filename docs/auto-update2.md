接下来的顺序建议按**依赖链倒推**,而不是按 spec 里的编号顺序,因为有几个环节互相卡着:

**第一步:先补掉简化项 1、2(这两个不做,后面全是假数据)**
- `SerializeRlState` 的三个字段置 0,意味着 PPO 训练用的状态是残缺的——先让策略侧把 `VaRFactor` 接进去,哪怕只是最简单的 rolling VaR 实现,也要把 `var_1d99` 填真实值。
- `RL_TRACE_PATH` 的 OnData 写入不做,trace schema 就只有 fixture 验证过,没有真实回测验证过。这个优先级最高,因为 Layer A/C 后面的"真实数据"全靠它。

**第二步:先跑通 test_layer_a_smoke,再谈 200 trials**
不要跳过 smoke test 直接上规模。3 次完整回测 × 每个 case,先在小范围(比如 5-10 trials)跑一次端到端,确认:
- Optuna 采样 → LEAN 回测 → DSR 计算 → 反馈闭环没有断点
- baseline bootstrap p-value 和 DSR 双阈值的判定逻辑在真实分布上不会全通过或全拒绝(这是最容易出静默 bug 的地方)
跑通后再扩到 200 trials,否则规模化后出错定位成本很高。

**第三步:Layer C 训练依赖 Layer A 产出的 trace,不要并行等待**
PPO 训练需要真实 trace 数据作为离线数据集,而 trace 又依赖第一步的 OnData 写入和 Layer A 跑出来的历史回测。所以 Layer C 实际上排在 Layer A smoke test 之后,不是独立并行任务。torch/onnxruntime 环境可以先在 CI 里装好、跑通 policy_exporter 的单测(哪怕用假数据),但真正的训练要等 trace 数据管够。

**第四步:Phase 6 第三策略接入放最后**
这是验证"通用化"是否成立的最终测试(BarraCNE5V4 只加 manifest,C# 不改代码),但只有前面三步都验证过、优化器和 RL 推理都产出可信结果后,接入才有意义——否则测的只是"能跑通"而不是"跑得对"。
