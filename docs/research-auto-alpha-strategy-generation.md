# 量化策略自动生成：开源框架调研报告（GitHub 检索版）

**日期**：2026-06-24（基于 GitHub 实际检索）
**背景**：SoloQuant 当前策略来源——① 爬权威站/论文 → LLM 抽取思路 → 复现；② 人工放策略思路到 `local-strategies/`。本报告调研"基于现有 tushare 数据自动生成量化策略"的高 star 开源框架，研究其优势与 LEAN 结合方式。
**检索范围**：GitHub topics（quantitative / algo-trading-framework，按 star 排序）+ 用户点名框架（TradeAgent/AlphaAgent）+ 公式化 alpha 挖掘 + LLM Agent 交易 + 工业平台。
**结论速览**：见 §8。最契合"tushare 数据→自动生成→LEAN"的是**公式化 alpha 挖掘框架**（AlphaGen / AlphaForge / gplearn / AlphaAgent）；高 star 的 **TradingAgents**（88.2k）是 LLM Agent 范式，适合决策生成但与 LEAN 事件驱动回测范式不同。

---

## 1. 三大类全景

| 类别 | 代表框架 | 生成物 | 与"数据→LEAN"契合度 |
|---|---|---|---|
| **A. 公式化 Alpha 挖掘** | gplearn、AlphaGen、AlphaForge、AlphaAgent、QuantaAlpha、Alpha-GFN | 可读 alpha 公式因子 | ★★★（因子→LEAN Alpha 模型，最干净） |
| **B. LLM Agent 交易** | TradingAgents（88.2k★）、AI-Trader、FinGPT | 每日交易决策/组合 | ★★（需桥接，范式不同） |
| **C. 工业级平台** | Qlib（微软）、FinRL | ML 信号/RL 策略 | ★★（回测与 LEAN 重叠冲突） |

---

## 2. A 类：公式化 Alpha 挖掘框架（最契合你的需求）

这类直接吃 OHLCV/财务面板，自动产出**数学公式因子**（如 `rank(ts_corr(close, vol, 10))`），可解释、可对接 LEAN。是"基于 tushare 数据自动生成"的最直接答案。

### 2.1 AlphaGen（RL-MLDM/alphagen）
- **原理**：强化学习（MCTS + RL policy）自动组合算子生成公式因子，直接优化**样本外 IC**，产出稀疏、协同的因子集。论文 *"Generating Synergistic Formulaic Alpha by RL"*。
- **优势**：抗过拟合（优化 OOS IC）、因子可读、学术验证充分、代码可跑。
- **tushare 结合**：仓库提供 `TensorData` 接口，把 tushare 的 `open/high/low/close/volume/amount` 转成张量喂入；算子池可自定义。
- **LEAN 结合**：输出的公式用 C# 在 LEAN `AlphaModel` 里实现（LEAN 有 `Identity/Rank/Mean/Correlation` 等算子），因子值→insights→backtest。
- 仓库：https://github.com/RL-MLDM/alphagen

### 2.2 AlphaForge（DulyHao/AlphaForge，AAAI 2025）
- **原理**：两阶段——① Alpha Factor Mining Network（生成-预测神经网络挖公式）；② Factor Timing Model（动态组合因子，带择时）。
- **优势**：不仅挖因子还**动态组合+择时**（多数框架只挖不组）、AAAI 顶会、针对 A 股验证。
- **tushare 结合**：输入 OHLCV+财务面板，输出因子公式与组合权重。
- **LEAN 结合**：因子公式→LEAN Alpha 模型；组合权重→LEAN `PortfolioConstructionModel`（按权重 `SetHoldings`）。
- 仓库：https://github.com/dulyhao/alphaforge

### 2.3 gplearn（heal-research/gplearn）
- **原理**：scikit-learn 风格遗传规划，`SymbolicTransformer` 按 IC/Sharpe 适应度进化公式。轻量、纯 sklearn。
- **优势**：最易上手、算子/适应度全可自定义、社区成熟、A股案例多。
- **劣势**：原生算子偏截面，时序算子（ts_mean/ts_corr）需自己包裹；易过拟合需严格 OOS。
- **LEAN 结合**：输出公式→C# 实现→LEAN Alpha 模型。
- 仓库：https://github.com/heal-research/gplearn

### 2.4 AlphaAgent（RndmVariableQ/AlphaAgent）
- **原理**：LLM 多阶段 Agent 流水线挖**可解释且抗 alpha 衰减**的因子（带正则化探索）。论文 arXiv 2502.16789。
- **优势**：专攻"alpha 衰减"痛点、LLM 生成可解释因子、多 Agent 协作（生成-评估-筛选）。
- **tushare 结合**：喂入数据画像/字段语义，LLM 提议因子公式并自评 IC。
- **LEAN 结合**：因子公式→LEAN Alpha 模型。
- 仓库：https://github.com/RndmVariableQ/AlphaAgent

### 2.5 QuantaAlpha（QuantaAlpha/QuantaAlpha）
- **原理**：LLM + 进化策略的**自进化**因子挖掘（trajectory-level self-evolution）——每次挖掘当作一条进化轨迹迭代。
- **优势**：自进化（无需人工 prompt 反馈）、LLM 假设生成 + 遗传变异结合、较新（2025-2026）。
- **LEAN 结合**：同上，因子公式→LEAN。
- 仓库：https://github.com/QuantaAlpha/QuantaAlpha

### 2.6 Alpha-GFN（nshen7/alpha-gfn）
- **原理**：用 **GFlowNet**（生成流网络）+ 深度 RL 挖公式因子，多样性优于普通 RL。
- **优势**：因子多样性高（GFlowNet 天然产出多模式解）、研究前沿。
- 仓库：https://github.com/nshen7/alpha-gfn

> 还有 **AlphaTransform**（kleonang，RL alpha 生成+回测一体）、**BrainAlpha**（多 Agent 复刻 alpha 发现全流程）等同类，可作备选。

---

## 3. B 类：LLM Agent 交易框架（高 star，范式不同）

这类用 LLM Agent 模拟"交易公司"（分析师/交易员/风控），读新闻+数据做**每日决策**。高 star、热门，但产物是"决策/组合"而非"可回测公式"，与 LEAN 事件驱动回测范式不同。

### 3.1 TradingAgents（tauricresearch/tradingagents）⭐ 88.2k
- **地位**：GitHub 最热门 AI 交易 Agent 框架（88.2k stars / 17k forks）。
- **原理**：多 Agent LLM 模拟真实交易公司——分析师（基本面/技术/情绪/新闻）→ 交易员 → 风控 → 投资组合，协作出决策。
- **优势**：拟真协作流程、v0.3.0（2026-06）扩展多 LLM provider（NVIDIA/Kimi/Groq/Mistral/Bedrock 等，**不绑定单一厂商**）、社区极活跃、有论文（arXiv 2412.20138）。
- **tushare 结合**：A 股数据可作为分析师 Agent 的输入（替换其默认美股数据源）。
- **LEAN 结合（需桥接）**：Agent 输出每日持仓/决策 → 导出为每日目标持仓 → LEAN 用 `SetHoldings` 复现历史。但**回测期每步需 LLM 调用**（成本高、非确定性），更适合**实盘/前向**而非历史回测。
- 仓库：https://github.com/tauricresearch/tradingagents

### 3.2 AI-Trader（HKUDS，FinGPT 团队）
- **原理**：FinGPT 团队的全自动化 Agent 原生交易系统。
- **优势**：FinGPT 血统、agent-native 设计。
- **LEAN 结合**：同 TradingAgents，决策→LEAN 持仓复现。
- 仓库：https://github.com/HKUDS/AI-Trader（同团队还有 Vibe-Trading）

### 3.3 A-Share Investment Agent（24mlight）
- **原理**：**专做 A 股**的多 Agent 投资助手，LLM 多角度解读市场。
- **优势**：原生 A 股、中文场景、多 Agent 协作。
- **tushare 结合**：天然契合（A 股数据直接喂）。
- 仓库：https://github.com/24mlight/A_Share_investment_Agent

> **B 类小结**：star 最高、最热门，但"LLM 每步决策"难做确定性历史回测，与 SoloQuant 的"可复现研究策略 + LEAN native 回测"理念有张力。更适合做**策略思路生成器**（让 Agent 提议策略，再人工/编译转成 LEAN 代码），而非直接交易执行。

---

## 4. C 类：工业级平台

### 4.1 Qlib（microsoft/qlib）— 高 star 工业平台
- **原理**：端到端 AI 量化——数据→因子库（Alpha158/Alpha360 预置）→ ML 模型（LightGBM/LSTM/Transformer）→ 组合优化→回测。
- **优势**：因子库完善（158/360 个）、ML pipeline 成熟、A股案例多、微软维护、社区活跃。
- **劣势**：**自带回测，与 LEAN native 原则冲突**；数据需转 Qlib bin 格式；学习曲线陡。
- **LEAN 结合**：建议**只借因子库**（Alpha158 公式）→ 用 C#/LEAN 重实现 + LEAN 回测，不用 Qlib 回测。或 Qlib 出预测信号→导出每日持仓 csv→LEAN 复现。
- 仓库：https://github.com/microsoft/qlib

### 4.2 FinRL（AI4Finance-Foundation/FinRL）
- **原理**：深度 RL 做交易（PPO/A2C/SAC），状态=市场因子窗口，动作=仓位。
- **优势**：择时/风控/动态仓位强、文档完善、A股有案例。
- **劣势**：黑盒（无公式）、过拟合风险高、回测需对接 LEAN。
- 仓库：https://github.com/AI4Finance-Foundation/FinRL

---

## 5. 对比矩阵

| 框架 | star | 生成物 | 可解释 | A股 | 与LEAN结合 | 实现成本 |
|---|---|---|---|---|---|---|
| **TradingAgents** | **88.2k** | 每日决策/组合 | 中 | 需改数据源 | ★★（决策→持仓复现，回测需每步LLM） | 中 |
| **Qlib** | 高 | ML 信号 | ★★ | 多 | ★★（借因子库；回测冲突） | 高 |
| **FinRL** | 高 | RL 策略 | ★ | 中 | ★★ | 中 |
| **AlphaGen** | 中 | 公式因子 | ★★★ | 可 | ★★★ | 中 |
| **AlphaForge** | 中 | 公式因子+组合 | ★★★ | 顶会验证 | ★★★ | 中高 |
| **gplearn** | 中高 | 公式因子 | ★★★ | 多 | ★★★ | 低 |
| **AlphaAgent** | 新 | 公式因子 | ★★★ | 可 | ★★★ | 中 |
| **QuantaAlpha** | 新 | 公式因子 | ★★★ | 可 | ★★★ | 中 |
| **Alpha-GFN** | 新 | 公式因子 | ★★★ | 可 | ★★★ | 中高 |
| **AI-Trader** | 中高 | 交易决策 | 中 | 需改 | ★★ | 中 |

> star 标注：仅 TradingAgents 确认 88.2k；其余标注"高/中/新"，精确数见各仓库页（star 数随时间变）。

---

## 6. 如何与 LEAN 结合（通用方案）

针对你的核心诉求"生成结果要能进 LEAN 回测"，三种集成模式：

**模式 1（最干净）：公式因子 → LEAN Alpha 模型**
- 适用于 A 类（AlphaGen/AlphaForge/gplearn/AlphaAgent/QuantaAlpha/Alpha-GFN）
- 流程：框架输出公式表达式 → 用 C# 在 LEAN `AlphaModel` 实现（LEAN 提供 `Rank/Mean/Std/Correlation/Delta` 等）→ `Update()` 算因子值 → 生成 `Insight` → LEAN 原生回测
- 优点：完全 LEAN native、可复现、确定性、契合 SoloQuant 现有 `extract_alpha_formulas_from_text` 基础设施

**模式 2：预测信号/每日持仓 → LEAN SetHoldings 复现**
- 适用于 Qlib/FinRL/B 类 Agent
- 流程：框架输出每日目标持仓 csv → LEAN 算法每日读 csv → `SetHoldings(symbol, weight)` 复现
- 优点：能用任何黑盒模型
- 缺点：B 类 Agent 回测期每步要调 LLM（成本/非确定性）；本质是"复现信号"而非"可解释策略"

**模式 3：Agent 生成策略思路 → 转成 LEAN 代码**
- 适用于 B 类（TradingAgents/AI-Trader/AlphaAgent 的 LLM 部分）
- 流程：Agent 提议策略思路/规则 →（编译验证）→ 生成 Lean C# 策略代码 → 进 SoloQuant 现有 codegen→compile→smoke→backtest 链路
- 优点：复用 SoloQuant 全套评估；产出可解释代码
- 缺点：Agent 提议质量需过滤

---

## 7. 针对 SoloQuant 约束的契合分析

- **LEAN native only** → 模式 1（公式因子）最契合；模式 2 可接受（信号复现）；模式 3 契合（转代码）。
- **永不修改成熟功能** → 新增独立阶段（如 `auto_mine_alpha`），不改现有 14 阶段。
- **可复现/可评估** → 公式因子最可复现；LLM Agent 决策最难复现。
- **数据已就绪**（tushare_data_v2 + cyq）→ A 类直接可用。

---

## 8. 推荐（按你的需求：tushare 数据驱动 + LEAN 集成 + 非 DeepSeek 绑定）

**第一梯队（公式化 alpha 挖掘，最契合）：**
1. **AlphaGen** —— RL 挖公式因子、优化 OOS IC、学术扎实、代码可跑。**首选 PoC 对象**。
2. **AlphaForge** —— AAAI 2025、挖+动态组合+择时、A 股验证。能力最全但要 GPU。
3. **gplearn** —— 最易上手、成本最低，适合快速验证"数据→公式→LEAN"链路。

**第二梯队（LLM Agent，高 star 但范式不同）：**
4. **TradingAgents（88.2k★）** —— 最热门、多 provider 不绑厂商；建议用作**策略思路生成器**（模式 3）而非直接执行。
5. **AlphaAgent / QuantaAlpha** —— LLM 挖可解释因子（模式 1），是 A 类与 B 类的交叉点。

**第三梯队（工业平台）：**
6. **Qlib** —— 借 Alpha158 因子库（C# 重实现进 LEAN），不用其回测。

**建议路径**：
- **PoC**：用 **AlphaGen**（或 gplearn）对 tushare daily+daily_basic 跑公式挖掘 → 输出因子 → C# 实现 → LEAN 回测 → 评估 OOS IC/Sharpe。
- **验证有效后**：做成 SoloQuant 新阶段 `auto_mine_alpha`（独立、不碰现有阶段），生成因子进 `strategy-registry.json` 走统一评估。
- **若想要 Agent 思路**：另起一条用 **TradingAgents** 生成策略思路→转 LEAN 代码的旁路（模式 3），与因子挖掘互补。

---

## 9. 参考链接

**A 类（公式化 alpha 挖掘）：**
- AlphaGen: https://github.com/RL-MLDM/alphagen
- AlphaForge (AAAI 2025): https://github.com/dulyhao/alphaforge
- gplearn: https://github.com/heal-research/gplearn
- AlphaAgent: https://github.com/RndmVariableQ/AlphaAgent （论文 arXiv 2502.16789）
- QuantaAlpha: https://github.com/QuantaAlpha/QuantaAlpha
- Alpha-GFN: https://github.com/nshen7/alpha-gfn
- AlphaTransform: https://github.com/kleonang/AlphaTransform

**B 类（LLM Agent 交易）：**
- TradingAgents (88.2k★): https://github.com/tauricresearch/tradingagents （论文 arXiv 2412.20138）
- AI-Trader (FinGPT 团队): https://github.com/HKUDS/AI-Trader
- A-Share Investment Agent: https://github.com/24mlight/A_Share_investment_Agent

**C 类（工业平台）：**
- Qlib (微软): https://github.com/microsoft/qlib
- FinRL: https://github.com/AI4Finance-Foundation/FinRL

**索引/精选列表：**
- Awesome-Applied-Agents-for-Investment: https://github.com/Sasha-Cui/Awesome-Applied-Agents-for-Investment
- GitHub topic: quantitative (按 star): https://github.com/topics/quantitative?o=desc&s=stars

---

*star 数仅 TradingAgents 经检索确认（88.2k）；其余请以仓库页实时为准。框架 API/版本请对照最新 README。报告已按反馈移除"复用 DeepSeek"路径，LLM 框架均支持多 provider（不绑定单一厂商）。*
