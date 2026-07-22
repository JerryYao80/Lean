**推荐 A（ZeroMQ/UDS IPC），但live paper环境建议改造成"离线训练 + 在线 fixed policy 推理"而非真正的在线学习。**
**交互层（IPC 机制选择）：A 优于 B、C**
**学习层（在线 PPO vs 离线训练）**
在线 RL 在实盘风控里有个致命问题：PPO 的 policy update 依赖 on-policy 的 rollout batch，实盘中你的"环境"（市场）非平稳、样本又稀疏（一天可能就几十个 rebalance 点），会导致：
- policy 在验证不充分的情况下被实时更新，等于让模型在真实资金上做探索（explore），这是不可接受的风险敞口；
- reward 信号（尤其接上一轮说的 DSR）本身是 trial 级/episode 级统计量，不适合做在线 per-step 更新的 dense reward，你会被迫用 shaped proxy reward，训练目标和你真正关心的 DSR 之间产生 gap。

**建议架构（融合 A 的 IPC + D 的训练模式）：**
1. **训练阶段**：离线用历史数据跑 Lean 回测生成状态序列 → Python 侧训练 PPO，reward 用 DSR（试验级）+ 逐笔 shaping reward（PnL/VaR 惩罚,方便 dense signal）。
2. **部署阶段**：训练好的 PPO policy 网络（不是查表，是保留网络本身，A股场景状态空间大概率是连续的，查表会有维度灾难）用 ONNX 或 TorchScript 导出，Python 侧跑一个轻量 inference server。
3. **实盘交互**：Lean 通过 A 方案的 ZeroMQ/UDS 向这个 inference server 请求 action，但**关闭在线梯度更新**——policy 参数在盘中固定,只做 forward inference。
4. **迭代**：每天/每周收盘后用新数据离线增量训练，人工审核 DSR 是否劣化后再替换线上 policy 版本（类似 CI/CD 的灰度发布,而不是让 agent 自己在线改自己)。

这样你既拿到了 A 的低延迟交互,又避开了纯在线 RL 在live paper上探索的风险,D 里"避开 IPC"的顾虑本质上是在担心在线学习的稳定性问题,用部署时 freeze 权重就能解决,不需要真的退化成静态查表。
