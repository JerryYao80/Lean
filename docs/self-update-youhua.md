## 总体判断

架构设计的大方向是对的——Layer A(离线贝叶斯)与 Layer C(冻结推理的 PPO)分离、过拟合五道防控链(CPCV/walk-forward/DSR/脊岭/基线对比)在同类开源实现里已经算严谨。但"自动进化"这个命名和框架的实际能力之间有落差：**目前更准确地说是"周期性重优化 + 冻结推理"，还不是持续进化**。有几个我认为是**方法论级别**的问题,比工程 bug 更需要优先解决。

## 重大纰漏

### 1. Layer C 的"环境"可能不是真环境——这是最大的风险点

`gym_env.py` 回放的是 `state_trace.jsonl`——一条**固定的历史真实轨迹**。如果这条轨迹里 `state[t+1]` 是历史真实值(不响应 PPO 输出的 `alpha` 动作重新计算),那么这个"environment"在数学上不是一个真正的 MDP 环境,而是一个**固定上下文的 bandit 问题**:policy 无论输出什么动作,下一步状态都不会变。

这会导致:
- PPO 是 on-policy 算法,假设"当前 policy 生成的动作影响未来状态分布"——固定轨迹回放违反这个前提,训练出的 policy 在真实部署时会遇到**分布不匹配**(policy 学的是"在别人已经做出某个仓位决策后的历史路径下,哪个缩放系数看起来不错",而不是"我把仓位缩放到 α 之后,未来真实会发生什么")。
- 实盘验证结果("composite 180 orders vs rl 155 orders,风控收紧代价 0.084%")看起来合理,但这只证明了 policy 在**同一条历史轨迹重放**下的行为,没有证明它在**真正响应自己动作的环境**里是最优甚至是安全的。

**这个问题需要先确认**:如果 `RlRiskEnv` 内部对 `alpha` 之后的 PnL/drawdown 路径做了**重新模拟**(哪怕是简化的线性缩放近似),那问题不大,只是保真度问题;如果没有,这是需要立刻修正的架构缺陷,而不是"已知简化"清单里排队等待的条目。

**建议**:如果确认是固定轨迹回放,应该改用**离线 RL(Offline RL)**算法而非 on-policy PPO——这类算法专门处理"训练数据是固定历史轨迹、无法真正交互"的场景,并且有对应的 Off-Policy Evaluation(OPE)方法在部署前估计真实收益,而不是只做 ONNX 数值一致性验证。

### 2. Layer A 与 Layer C 目标函数不统一

Layer A 的 reward 是原始 Sharpe(平稳、事后 DSR 门禁),Layer C 的 reward shaping 由 `var_budget`/`max_dd` 驱动。这是两个独立的优化目标,没有联合约束。可能出现:Layer A 搜出一组"高 Sharpe 但高波动"的策略参数,再交给 Layer C 去"补救"控制风险——本质上是两个优化器在互相拉扯,而不是一个统一的进化目标。真正的进化闭环应该让 Layer A 的目标函数**知道** Layer C 存在(比如用"Layer C 介入后的净 Sharpe"作为 Layer A 的 reward,或者做多目标优化)。

### 3. 触发器全部是滞后指标,没有领先指标

"数据累积到 504 天 / Sharpe 衰减 30% / kill-switch 触发 / 人工周期"——全部是事后反应式的。等到 Sharpe 已经衰减 30% 才重新优化,对市场机制切换(regime shift)的响应已经太慢。缺一个基于**特征分布漂移检测**的领先触发器。

### 4. 灰度上线缺 shadow mode 和 champion-challenger

`deploy_gate: manual` + `rollout_policy: greedy` 直接跳到"人工审核后全量/灰度",没有:
- 新旧 policy 在**共享验证状态集**上的行为差异度量(比如动作分布 KL 散度)——防止一次重训后 policy 行为发生剧变而没有门禁拦住
- 影子模式(shadow):新 policy 先只记录预测动作、不真实影响下单,和当前冠军策略并行跑一段时间再切换

### 5. RL state 字段置 0 的训练偏差被低估了

`pnl_1d`/`days_held`/`drawdown` 在 BarraCNE5V4/OvernightAnomaly 首期置 0,清单里标"已知简化,后续补"。但这不只是"简化"——**这些策略上训练出的 policy 实际上是在学习"当这些字段恒为 0"这个错误假设下的最优动作**,一旦补上真实值,之前训好的 policy 大概率整体失效,需要重训而非增量修补。建议要么这几个策略暂不接入 Layer C(排除出灰度范围),要么标记为"实验性/不可信",不要和 OptionVolArb5Layer(真实值)同等对待。

## 结合 SOTA 开源框架的优化方案

| 问题 | 现状 | 建议框架/方法 |
|---|---|---|
| PPO 训练于固定轨迹,分布不匹配 | stable-baselines3 PPO(on-policy) | 改用 **d3rlpy**(开源离线 RL 库,内置 CQL / IQL / TD3+BC),配合 **FQE(Fitted Q Evaluation)** 做部署前的离策略价值估计,量化"这个 policy 大概率能带来多少改进"而不是只验证 ONNX 数值一致性 |
| 纯 reward shaping 控风险,没有显式约束 | 手工权重 var_budget/max_dd 加进 reward | **OmniSafe**(统一 Safe RL 框架,内置 PPO-Lagrangian / CPO)或 Ray RLlib 的约束变体——把 VaR/回撤上限建成硬约束而非软惩罚项,更贴近风控本意,且约束违反率可直接监控 |
| Layer A 每周期从零跑 200 trials,成本随数据增长线性上升 | Optuna TPE 全量重扫 | 开启 Optuna 内置的 **HyperbandPruner / SuccessiveHalvingPruner** 做多保真度剪枝(短窗口粗筛→长窗口精调),并复用旧 study(warm start)而非新建 sqlite,把"进化"真正做成有历史记忆的搜索而不是每次重新探索 |
| Layer A/C 目标函数不统一 | 各自独立 reward | Optuna 的 **多目标采样器(NSGA-II/MOTPE)**,同时优化 Sharpe 与(Layer C 介入后的)最大回撤,输出 Pareto 前沿而非单一冠军,人工在 Pareto 集里选,而不是让 Layer C 事后"擦屁股" |
| 触发器均为滞后指标 | Sharpe 衰减 30% 才触发 | 引入 **River**(在线机器学习库)的 ADWIN/DDM 漂移检测器,监控关键因子(如筹码集中度、IV/HV z-score)的分布漂移,作为领先触发信号,比等 Sharpe 衰减更早介入 |
| 灰度上线无 shadow/champion-challenger | 直接人工审核后部署 | 引入影子流量记录(新 policy 只记录不执行)+ 新旧 policy 动作分布 KL 散度作为**第六道门**;比例爬坡(canary)而非一步到位 |
| RL 环境是否真实响应动作待确认 | gym_env 回放 trace | 若确认非交互式,短期用离线 RL + OPE 过渡;中期考虑参考 **FinRL**(开源金融 RL 框架)的环境抽象,构建一个响应仓位缩放动作、带简化冲击成本模型的轻量模拟器,让训练环境真正满足 MDP 假设 |

## 优先级建议

1. **先确认第 1 点(gym_env 是否真实响应动作)**——这决定了 Layer C 当前的验证结果("composite vs rl 收益对比")是否有效,是地基问题,必须先查清楚再谈其他优化。
2. 第 5 点(置 0 字段策略)建议立刻把 BarraCNE5V4/OvernightAnomaly 移出灰度候选,防止用一个已知有偏的 policy 影响真实资金。
3. 其余(多目标优化、漂移检测、shadow mode)可以按迭代节奏逐步补,不阻塞当前闭环运行,但应该写进 backlog 而不是散在"已知简化"里被淡化。