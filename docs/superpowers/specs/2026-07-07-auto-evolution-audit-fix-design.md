# 量化策略自动进化功能审计修复设计（A 阶段：离线 RL + OPE）

**日期:** 2026-07-07
**状态:** Draft → 待用户审核
**依据:** `docs/self-update-youhua.md`（审计）+ `docs/self-updatea.md`（Layer C 修复路径）+ `docs/self-update23.md`（第5点处理）

---

## 0. 范围

针对 `docs/self-update-youhua.md` 审计的 5 大纰漏 + 7 项 SOTA 优化方案，分阶段实施：

- **本期（A 阶段）**：Layer C 地基修复（离线 RL + OPE）+ 第5点移出候选/自动门禁/字段审计
- **backlog**：多目标优化、漂移检测、shadow mode、Hyperband 剪枝、B 阶段轻量 MDP 模拟器

**三个立足约束不变**：A股真实标的 + tushare 真实数据 + LEAN 原生接口（Bayesian/RL 用 Python 成熟库 d3rlpy，不自写）。

---

## 1. A 阶段：Layer C 地基修复（离线 RL + OPE）

### 1.1 问题

`gym_env.py` 回放固定历史轨迹 `state_trace.jsonl`，`state[t+1]` 是历史真实值，不响应 PPO 输出的 `alpha` 动作。这在数学上不是真 MDP，而是固定上下文 bandit。PPO 是 on-policy，假设动作影响未来状态分布——固定轨迹回放违反此前提，训练出的 policy 有分布不匹配风险。

现有 live-paper 验证（"composite 180 orders vs rl 155 orders"）只证明 policy 在**同一条历史轨迹重放**下的行为，未证明在**真正响应自己动作的环境**里最优甚至安全。

### 1.2 A1：alpha 方差诊断 + 动作多样化重跑

**前置检查（self-updatea.md 警告）**：CQL/IQL 能学到东西，前提是数据里对"同一类状态"存在不同动作的真实结果。现有 trace 大概率是 composite 策略（alpha 恒为 1）生成，动作维度无变化。若方差≈0，离线 RL 静默失效（CQL 保守惩罚让 policy 收敛到 alpha≈1，且 FQE 评估可能还显示"没变坏"）。

**新增文件**：

| 文件 | 职责 |
|---|---|
| `Scripts/auto_optimize/alpha_variance_check.py` | 扫描现有 trace，统计 alpha 分布方差。方差≈0 → 触发多样化重跑 |
| `Scripts/auto_optimize/alpha_perturbation_runner.py` | 跑 5-10 组固定 alpha 扰动（0.3/0.5/0.7/0.9/1.0）的 LEAN 回测，通过 `risk-mode=rl` + `rl-fallback-alpha={α}` 注入（fallback 即固定 α）。每组产出一条 trace，合并成 `state_trace_diverse.jsonl` |

**实现要点**：
- **不改 gym_env**，只是多跑几次已有 LEAN 回测，成本很低
- 每组 alpha 跑同一回测窗口，trace 文件按 alpha 命名（如 `state_trace_a0.3.jsonl`），合并时标注 `alpha` 字段
- 输出：`Results/auto_optimize/{strategy}/state_trace_diverse.jsonl` + alpha 方差报告

### 1.3 A2：d3rlpy 离线 RL + FQE 评估

**新增文件**：

| 文件 | 职责 |
|---|---|
| `Scripts/auto_optimize/offline_rl_trainer.py` | 用 d3rlpy 的 CQL 或 IQL 训练。输入 = A1 多样化 trace（state/action/reward/next_state 四元组），输出 = policy.pt |
| `Scripts/auto_optimize/ope_evaluator.py` | 用 d3rlpy 的 FQE（Fitted Q Evaluation）做部署前离策略价值估计，量化"policy 大概率能带来多少改进" |

**算法选择**：
- 默认 **CQL**（保守 Q 学习，对分布外动作有保守惩罚，适合风控场景）
- 备选 **IQL**（隐式 Q 学习，对数据质量更鲁棒）
- 通过 `config.yaml` 的 `offline_rl.algorithm: cql|iol` 切换

**数据转换**：d3rlpy 需要标准 MDP 元组 `(s, a, r, s', done)`。新增 `trace_to_mdp_dataset.py` 把 `state_trace_diverse.jsonl` 转成 d3rlpy `MDPDataset`。alpha 作为 action，每条 trace 的 shaping reward 作为 r，next bar 状态作为 s'。

**ONNX 导出**：`policy_exporter.py` 复用（d3rlpy policy 内部是 PyTorch），但验证逻辑增加 FQE 估计值对比（不仅 ONNX 数值一致性）。

### 1.4 A 阶段不做

- 不改 gym_env 为真 MDP（留 B 阶段）
- 不引入 FinRL（与零侵入设计哲学冲突，self-updatea.md 已否决）
- 不用 OmniSafe 约束 RL（A 阶段先用软惩罚 shaping）

---

## 2. 第5点处理：移出候选 + manifest 自动门禁 + 字段补全

遵循 self-update23.md 的"A 立即执行 + C 用修复后 pipeline 重训"。

### 2.1 A：移出候选 + manifest 自动门禁（立即执行，成本≈0）

**问题**：BarraCNE5V4/OvernightAnomaly 的 `pnl_1d`/`days_held`/`drawdown` 置 0，policy 学到"系统性错误假设下的确定性映射"，对置 0 状态给出**自信且错误**的动作。这是二元判断（该阶段不能用），不是权重问题（不能用降权处理）。

**实现**：
- **manifest 加字段**：`rl_state_completeness: full | partial`
  - `full`：所有 state_schema 字段填真实值
  - `partial`：有字段置 0
- **manifest_lint.py（门 0）自动拦截**：`partial` 策略不允许进入灰度部署流程
- **现有 4 策略标记**：
  - option_vol_arb_5layer: `full`（但需先审计，见 §2.3）
  - var_strategy: 待审计（SerializeRlState 较简化）
  - barra_cne5_v4: `partial`
  - overnight_anomaly: `partial`

**关键**：把"移出候选"从一次性人工动作升级为框架自动强制执行的规则。新策略接入若漏填字段，门 0 自动拦下。

### 2.2 C：字段补全（用修复后 pipeline 重训）

**关键约束（self-update23.md 第一点）**：不先用旧 PPO 训一遍再返工。两个修复（地基 + 字段）最终在同一套新 pipeline（§1 的 d3rlpy + FQE）上收口。

**顺序**：
1. 现在：§2.1 移出候选 + 自动门禁（止血）
2. 与 §1 地基修复并行：审计 OptionVolArb5Layer 字段计算逻辑（§2.3）
3. §1 地基方法论确定后：补 BarraCNE5V4/OvernightAnomaly 真实字段，用新 pipeline 重训

### 2.3 OptionVolArb5Layer 字段审计（参照核实）

self-update23.md 警告：照抄模板前，先核实参照本身的字段计算逻辑。

**新增文件**：`Scripts/auto_optimize/option_vol_arb_field_audit.py`

**审计项**：

| 字段 | 计算逻辑 | 审计点 |
|---|---|---|
| `pnl_1d` | `_prevClose` 字典，SerializeRlState 在 `_prevClose` 更新前调用 | 写入顺序是否正确（auto-update6 曾调整过） |
| `days_held` | `_entryDay` 字典 + `_dayIndex` | 边界情况：策略首日建仓时 `_entryDay` 是否正确初始化 |
| `drawdown` | `_peakTpv` 跟踪 | 口径是否与 LEAN 回测 statistics 的 Drawdown 一致（LEAN 基于 equity curve 最大回撤） |

**实现**：对比 SerializeRlState 输出的字段值 vs LEAN 回测 statistics 的对应值，确认口径一致。不一致则修正 OptionVolArb5Layer，再作为模板推广。

---

## 3. 其余审计点（backlog）

self-update-youhua.md 第 2/3/4 点 + SOTA 优化表未在 §1/§2 覆盖的项。不阻塞当前闭环，按迭代节奏补。

### 3.1 第2点：Layer A 与 Layer C 目标函数统一（多目标优化）

- `bayesian_optimizer.py` 切换 Optuna 多目标采样器（NSGA-II / MOTPE），同时优化 Sharpe 与（Layer C 介入后的）最大回撤，输出 Pareto 前沿
- 人工在 Pareto 集里选，而非让 Layer C 事后擦屁股
- 依赖 §1 地基修复完成

### 3.2 第3点：领先触发器（漂移检测）

- 新增 `drift_detector.py`：用 River 库的 ADWIN/DDM 在线漂移检测器，监控关键因子（筹码集中度、IV/HV z-score、VaR regime）分布漂移
- 作为领先触发信号，比等 Sharpe 衰减更早介入
- 触发条件加入 `config.yaml` 的进化调度

### 3.3 第4点：shadow mode + champion-challenger

- 新增 `shadow_mode.py`：新 policy 先只记录预测动作、不真实影响下单，与冠军策略并行跑
- 新增第六道门：新旧 policy 动作分布 KL 散度（防止重训后行为剧变）
- 比例爬坡（canary）：5% → 25% → 100%

### 3.4 Optuna Hyperband 剪枝 + warm start

- `bayesian_optimizer.py` 开启 HyperbandPruner / SuccessiveHalvingPruner（短窗口粗筛 → 长窗口精调）
- 复用旧 study（warm start）而非新建 sqlite，让"进化"有历史记忆

### 3.5 明确排除（本期不做）

- **OmniSafe 约束 RL**（PPO-Lagrangian/CPO）：A 阶段先用软惩罚 shaping
- **FinRL**：与零侵入设计哲学冲突，self-updatea.md 已否决
- **完整市场模拟器**（冲击成本/订单簿）：B 阶段做轻量"仓位缩放重算"模拟器即可

### 3.6 backlog 汇总

| 项 | 优先级 | 依赖 |
|---|---|---|
| 多目标优化（NSGA-II） | 中 | §1 地基修复完成 |
| 漂移检测触发器 | 中 | River 库集成 |
| shadow mode + KL 散度门 | 中 | §1 完成 + live-paper 稳定 |
| Hyperband 剪枝 + warm start | 低 | 无 |
| B 阶段轻量 MDP 模拟器 | 低 | §1 A 阶段验证 policy 有效后 |

---

## 4. 文件结构（新增）

### A 阶段新增 Python 文件

| 文件 | 职责 | 依赖 |
|---|---|---|
| `Scripts/auto_optimize/alpha_variance_check.py` | alpha 方差诊断 | 现有 trace |
| `Scripts/auto_optimize/alpha_perturbation_runner.py` | 5-10 组固定 alpha 扰动重跑 | lean_runner |
| `Scripts/auto_optimize/trace_to_mdp_dataset.py` | trace → d3rlpy MDPDataset | A1 多样化 trace |
| `Scripts/auto_optimize/offline_rl_trainer.py` | d3rlpy CQL/IQL 训练 | d3rlpy |
| `Scripts/auto_optimize/ope_evaluator.py` | FQE 离策略价值估计 | d3rlpy |
| `Scripts/auto_optimize/option_vol_arb_field_audit.py` | OptionVolArb5Layer 字段审计 | 现有 trace + stats |

### 修改文件

| 文件 | 修改 |
|---|---|
| `Scripts/auto_optimize/manifest_loader.py` | 解析 `rl_state_completeness` 字段 |
| `Scripts/auto_optimize/manifest_lint.py` | 门 0 检查 `rl_state_completeness`，`partial` 不允许灰度 |
| `Scripts/auto_optimize/strategies/*/manifest.yaml` | 4 策略加 `rl_state_completeness` 字段 |
| `Scripts/auto_optimize/config.yaml` | 加 `offline_rl.algorithm: cql` 配置 |

### 测试

| 文件 | 范围 |
|---|---|
| `Tests/Python/test_alpha_variance_check.py` | alpha 方差诊断正确性 |
| `Tests/Python/test_trace_to_mdp_dataset.py` | trace→MDP 转换正确性（四元组完整性） |
| `Tests/Python/test_offline_rl_trainer.py` | d3rlpy 训练 smoke（少量数据，验证不崩溃） |
| `Tests/Python/test_ope_evaluator.py` | FQE 评估输出合理值 |
| `Tests/Python/test_manifest_lint_completeness.py` | `partial` 策略被门 0 拦截 |

---

## 5. 验证

### 5.1 A1 验证（alpha 方差 + 多样化重跑）

- alpha_variance_check 报告显示原始 trace alpha 方差≈0
- 5 组扰动重跑产出 5 条 trace，合并后 alpha 方差 > 阈值
- `state_trace_diverse.jsonl` 行数 = 原始 × 5

### 5.2 A2 验证（d3rlpy + FQE）

- CQL/IQL 训练不崩溃，policy.pt 产出
- FQE 估计值 > 现有 PPO policy 的 FQE 估计值（证明离线 RL 学到东西）
- ONNX 导出一致性验证通过（atol=1e-5）

### 5.3 第5点验证（自动门禁）

- manifest_lint 对 `partial` 策略报错，`full` 通过
- BarraCNE5V4/OvernightAnomaly 标记 `partial` 后被门 0 拦截

### 5.4 字段审计验证

- OptionVolArb5Layer SerializeRlState 输出的 `drawdown` 与 LEAN statistics 的 Drawdown 口径一致（差异 < 1%）
- `pnl_1d`/`days_held` 边界情况正确

---

## 6. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| d3rlpy 未安装 | `pip install d3rlpy`，CI 环境装包 |
| CQL 收敛到 alpha≈1（数据不足） | A1 多样化重跑确保 alpha 方差；FQE 对比验证 |
| OptionVolArb5Layer 字段口径不一致 | §2.3 审计先行，修正后再作为模板 |
| 字段补全后旧 policy 失效 | self-update23.md 已明确：用修复后 pipeline 重训，不增量修补 |
| B 阶段轻量模拟器近似误差 | B 阶段切换前抽样对比近似值 vs 真实 LEAN 重跑值 |

---

## 7. 实现顺序

1. **Phase A1**：alpha_variance_check + alpha_perturbation_runner + 测试
2. **Phase A2**：trace_to_mdp_dataset + offline_rl_trainer + ope_evaluator + 测试
3. **Phase 第5点 A**：manifest 加 `rl_state_completeness` + manifest_lint 拦截 + 4 策略标记
4. **Phase 第5点 C 准备**：option_vol_arb_field_audit 审计（与 A 并行）
5. **Phase 第5点 C 执行**：补 BarraCNE5V4/OvernightAnomaly 真实字段，用新 pipeline 重训（依赖 A2 完成）

---

## 8. 引用

- `docs/self-update-youhua.md` — 审计 5 大纰漏 + 7 项 SOTA 优化
- `docs/self-updatea.md` — Layer C 修复路径（C 分阶段，A 前置 alpha 方差检查）
- `docs/self-update23.md` — 第5点处理（A 移出候选+自动门禁，C 用修复后 pipeline 重训）
- `docs/self-update10.md` — 自动进化功能实现原理（已实现部分）
- d3rlpy: https://github.com/takuseno/d3rlpy（离线 RL 库，CQL/IQL + FQE）
