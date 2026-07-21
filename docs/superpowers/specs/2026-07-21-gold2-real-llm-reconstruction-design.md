# Gold2 G3-Real(真 LLM 重构)设计规格

> **目标**:用真 LLM(glm-5.2 via mydamoxing.cn)替换 G3 阶段的 `_StaticGen` 桩,产出**可编译、可冻结、可盲测**的真重构候选,针对性修复 G2 shaping 的两个经济缺陷(牛市踏空 + 下跌无保护),并为后续沪深300/科创50 接入提供**统一接口**。
>
> **日期**:2026-07-21
> **分支**:`fix/price-scaling-10000x`(worktree `gold2-closed-loop-proof`)
> **范围档**:跨 spec §6 的 LLM 隔离档(诚实标注),但通过**独立模块 + 不 import production machinery + 注入 runner** 保持隔离。
>
> **硬线**:`never-modify-lean-native`(不动 LEAN 核心 C#)、`never-modify-existing-features`(不改 Gold2BetaVolTargetStrategy 等成熟 feature 既有行为,C# 仅新增+默认中性)、反 p-hacking(网格/门槛/seed 不变,LLM prompt 零盲年数据,freeze 先于 open)。

---

## 0. 背景与 3 个战略问题

`docs/gold2-4phase.md` 的三阶段回测显示:G2 shaping 在 518880 的 2022-2025 盲年上**全面跑输 G0**(4/4 ΔSharpe 为负,aggregate_delta=-2.7822)。两个具体经济缺陷:

1. **牛市踏空**:G2 在 W3/W4 上涨盲年把正收益从 +7.3%→+3.6%、+17.7%→+6.9%。
2. **下跌无保护**:G2 在 W1 下跌年非但没保护,反而放大亏损(Sharpe −0.23→−1.56)。

根因诊断(已在 `docs/check-gold2-dtls2.md` 定位):G2 shaping 在 `RISING_FAST` regime **一刀切收紧 realrate cap**(`Gold2RealRateCapModel.cs:55-57` `capFactor = regime==RISING_FAST ? effRealRateCap : 1.0`),上涨 regime 也削仓位→踏空;且 cap 是绝对值缩放,不区分趋势方向,下跌年也没提供额外保护。

本设计须显式回答 3 个战略问题:

- **Q-A 普遍价值**:G2 shaping 重构的依据(4 层 telescoping 归因:`trend`/`vol_target`/`extreme_risk`/`realrate_cap` 的 `pnl_pct_of_total`)是否具备跨标的的普遍价值?
- **Q-B 牛市踏空**:为什么大牛市期间错失机会,同时下跌年没保护?重构如何针对性修复?
- **Q-C 多标的统一**:后续增加沪深300/科创50 时如何统一设计,不至于一个标的一套实现?

---

## 1. 架构 — 虚函数钩子替换(方案 2)

### 1.1 新建基类(独立目录,不动 Gold2BetaVolTargetStrategy)

新建 `Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs`。基类与 `Gold2BetaVolTargetStrategy.cs:56-109` 的 `Initialize()` 逐段一致(同 SetCash/Start/End、同 AddEquity、同 factors、同 Universe/Alpha/Portfolio/Execution),**唯一不同**是把 `Gold2BetaVolTargetStrategy.cs:106-107` 的两条 `AddRiskManagement(...)` 换成对一个虚函数钩子的调用:

```csharp
public class Gold2ReconstructionCandidateBase : QCAlgorithm, IOptimizableStrategy, IRlStateExportable
{
    protected Symbol _gold, _auSym, _vixSym, _dfii10Sym;
    protected Gold2ExtremeRiskFactor _ext;
    protected Gold2RealRateCapFactor _realrate;
    protected decimal _extremeCap, _effExtremeCap, _effRealRateCap;
    // ... 其余字段同 Gold2BetaVolTargetStrategy

    public override void Initialize()
    {
        // 与 Gold2BetaVolTargetStrategy.Initialize() 56-105 完全一致
        // (SetCash/Start/End, AddEquity(spec), factors, Universe/Alpha/Portfolio/Execution)
        // 唯一不同:line 106-107 的两条 AddRiskManagement 换成:
        foreach (var model in BuildRiskModels()) AddRiskManagement(model);
    }

    /// <summary>
    /// 虚函数钩子。默认返回 G0 的两个 Risk Model —— 与
    /// Gold2BetaVolTargetStrategy.cs:106-107 行为逐字节相同(G0/G3Real-default 路径不变)。
    /// LLM 生成的子类 override 此方法,返回 regime 非对称的 Risk Model 组合。
    /// </summary>
    protected virtual IEnumerable<IRiskManagementModel> BuildRiskModels()
        => new IRiskManagementModel[]
        {
            new Gold2ExtremeRiskModel(_ext, _gold, _effExtremeCap),
            new Gold2RealRateCapModel(_realrate, _gold, _effRealRateCap),
        };

    // GetTunableParameterNames / SerializeRlState / Get*Parameter helpers
    // 全部照搬 Gold2BetaVolTargetStrategy(同接口契约,保证 G1/复盘 adapter 兼容)。
}
```

### 1.2 G0 byte-identical 守恒论证

基类默认 `BuildRiskModels()` 返回的两个 model 实例 = `Gold2BetaVolTargetStrategy.cs:106-107` 创建的同一组:
- `new Gold2ExtremeRiskModel(_ext, _gold, _effExtremeCap)`(3-arg ctor,`Gold2ExtremeRiskModel.cs:22-27`)
- `new Gold2RealRateCapModel(_realrate, _gold, _effRealRateCap)`(3-arg ctor,`Gold2RealRateCapModel.cs:35-39`)

`foreach(var m in BuildRiskModels()) AddRiskManagement(m)` 与连续两条 `AddRiskManagement(...)` 在 `CompositeRiskManagementModel` 中语义等价(都是 append 到 `_riskManagementModels` 链,顺序一致 → 串联 `min` 行为一致)。

因此 **G0/G3Real-default 路径 = 现状逐字节相同**。成熟 G0 行为不变;只有当 LLM 子类 override `BuildRiskModels()` 返回不同 model 时,G3Real 行为才偏离 G0。

### 1.3 LLM 生成的子类(只 override BuildRiskModels)

子类源码写在 `<candidate_dir>/G3.cs`(`g3_builder.py:236-267` 已强制候选目录在 `proof_root/candidates/<candidate_id>/` 下,**不进** `Algorithm.CSharp` 树):

```csharp
using ...;
namespace QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction
{
    public class Gold2G3Real_<candidate_id_safe> : Gold2ReconstructionCandidateBase
    {
        protected override IEnumerable<IRiskManagementModel> BuildRiskModels()
        {
            // LLM 填:regime 非对称修复(见 §3.5)
            yield return new Gold2RegimeAsymmetricRiskModel(
                _ext, _realrate, _gold, _effExtremeCap, _effRealRateCap);
        }
    }
}
```

子类**只** override `BuildRiskModels()`,不许碰 Universe/Alpha/Portfolio/Execution/Initialize 主体 —— 编译期 + 运行期 reflect 双重校验(§5)。

---

## 2. 多标的统一(Q-C 的答案)

### 2.1 统一点 = Gold2InstrumentSpec factor registry

新建 `Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2InstrumentSpec.cs`:

```csharp
public sealed record Gold2InstrumentSpec(
    string Ticker,
    string Market,                          // "SSE" / "CSI"(待加) / "STAR"(待加)
    Type GoldDataSource,                    // 518880→AuShfDailyBar; 300/50→各自现货/期货源类型
    string GoldDataTicker,                  // "AU.SHF" / 现货代码
    Type MacroVixDataSource, string VixTicker,
    Type MacroRealRateDataSource, string RealRateTicker,
    Func<Symbol, decimal, Gold2RealRateCapFactor> RegimeFactorFactory,
    Func<decimal, decimal, int, decimal, Gold2VolRegimeFactor> VolFactorFactory);

public static class Gold2InstrumentRegistry
{
    public static Gold2InstrumentSpec Get(string instrument) => instrument switch
    {
        "518880" => new Gold2InstrumentSpec("518880", "SSE", typeof(AuShfDailyBar), "AU.SHF",
                        typeof(FredMacroData), "VIX", typeof(FredMacroData), "DFII10",
                        (g, cap) => new Gold2RealRateCapFactor(new GoldRealRateRegimeFactor(), cap),
                        (l, vt, w, a) => new Gold2VolRegimeFactor(l, vt, w, a)),
        // 000300 / 000688 接入 = 在此加一条 spec,基类/钩子/复盘/盲测全部复用
        _ => throw new ArgumentException($"unknown instrument: {instrument}")
    };
}
```

### 2.2 基类 Initialize 用 spec 驱动

基类 `Initialize()` 第一行:
```csharp
var spec = Gold2InstrumentRegistry.Get(GetParameter("instrument", "518880"));
```
随后 `AddEquity(spec.Ticker, Resolution.Daily, spec.Market)`、`AddData<...>(spec.GoldDataTicker)`、factor 构造全部用 `spec` 的 factory。

### 2.3 沪深300/科创50 接入 = 加一条 registry 条目

不写第二份策略、不写第二份基类、不写第二份复盘。`Gold2InstrumentSpec` 注册新 instrument 的 ticker/market/data source/factor factory 即可。可比性保证:同基类、同 `BuildRiskModels` 钩子、同 telescoping 归因(`formal_review.py:236-308` fill-notional 拆分与 instrument 无关)、同盲测协议(`BlindEvaluator`)。

### 2.4 Q-A 普遍价值的答案

4 层 telescoping 归因是 **fill-notional 按方向拆分**(`formal_review.py:236-308`:`qty>0→extreme_risk`、`qty<=0→realrate_cap`、fees→extreme、trend/vol_target 残差),与具体 instrument 无关 —— 对 518880 / 沪深300 / 科创50 同构。LLM 重构的依据(归因 gap)天然跨标的;`Gold2InstrumentSpec` 让"依据普遍、输入各异"成为可能。

---

## 3. LLM 契约 + 针对性修复(Q-B 的答案)

### 3.1 prompt 输入(严格 TRAIN 期,零盲年)

| # | 输入 | 来源 | 盲年泄漏检查 |
|---|---|---|---|
| 1 | `layer_attribution`(trend/vol_target/extreme_risk/realrate_cap 的 `pnl_pct_of_total`) | P2 frozen review bundle `result/gold2-p2-construction/<W>/review/review-bundle-*.json` | bundle 严格 TRAIN(`construct_p2.py:89` 跑 `w.train`;`feedback_construction.py:25-29` 仅用 `{wid}/train`+`{wid}/review`) |
| 2 | `Gold2BetaVolTargetStrategy.cs` 全文 + `Gold2ExtremeRiskModel.cs` + `Gold2RealRateCapModel.cs` | 源码文件 | 源码无盲年数据 |
| 3 | `GetTunableParameterNames()` 11 项 + shaping 倍率语义(`extreme_risk_contrib_penalty`/`realrate_cap_contrib_penalty` 默认 1.0) | `Gold2BetaVolTargetStrategy.cs:180-185` + spec | 无盲年 |
| 4 | 诊断约束(明文) | 本规格 §0/§3.5 | 仅引用 TRAIN 期 metrics + 已封存的 blind 期 ΔSharpe 结论作"已知失败模式"(ΔSharpe 是聚合裁决,非逐 bar 盲年数据,不构成泄漏) |
| 5 | 基类 `Gold2ReconstructionCandidateBase` skeleton + 接口约束(只 override `BuildRiskModels`) | 本规格 §1 | 无盲年 |

### 3.2 零盲年保证(反 p-hacking硬线)

- prompt 里只有 `<W>/train` 和 `<W>/review` 的 bundle(`feedback_construction.py:25-29` 保证 bundle 严格 TRAIN 期)。
- 盲年起止年份、盲年价格、盲年 metrics **不进 prompt**。
- 候选冻结(`G3Builder.build` `g3_builder.py:224,275-279` 算 `source_sha256`/`candidate_set_sha256`)**先于** `open_blind`(`blind_evaluator.py:320-390`)。时序上 LLM 不可能拿到盲年数据。
- `assert_no_post_blind_mutation`(`blind_evaluator.py:472-479`)禁止 blind 后 generation/repair/retry/reselection/config change。

### 3.3 glm-5.2 HTTP 机制(独立模块,不 import production)

新建 `Scripts/gold2_closed_loop/g3_real_llm_client.py`:

```python
import requests, json

def generate_reconstruction(prompt: str, *, base_url="https://mydamoxing.cn",
                            auth_token="sk-Lqf1cFD3252DX3Jt4lXWa3C2lCD3U5bwSdFoUwUIJZWD6Dpw",
                            model="glm-5.2", timeout=120) -> str:
    """Mirror soloquant_orchestrator.py:3402 summarize_with_llm 的 HTTP shape.
    独立实现,不 import soloquant_orchestrator(§6 隔离 + _FORBIDDEN_REFERENCES 不匹配)."""
    resp = requests.post(f"{base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
```

`_FORBIDDEN_REFERENCES = ("evolution_scheduler","layer_state","evolution_state","generation_state")`(`interface_readiness.py:33-38`)whole-token 匹配 —— 新模块名 `g3_real_llm_client` / `construct_p3_real` 均不匹配,§6 隔离守住。

### 3.4 LLM 输出 → 编译 → 冻结 → gate

1. LLM 返回 C# 子类源码 → `G3Builder.build` 已做 `atomic_write_bytes(candidate_dir/"G3.cs", source_text)`(`g3_builder.py:263-267`)。
2. **编译 harness**:`G3RealGenerator` 在 `candidate_dir/` 放一个 `.csproj` 模板(新文件 `Scripts/gold2_closed_loop/g3_real_candidate.csproj.tmpl`),`<Reference>`/`<ProjectReference>` 指向 `Algorithm.CSharp.csproj` + `Common.csproj` + `Engine.csproj`(基类 `Gold2ReconstructionCandidateBase` 与 Risk Model 类都在 `Algorithm.CSharp`)。然后 `dotnet build candidate_dir/<candidate_id>.csproj -o candidate_dir/bin/` → 产出 `<candidate_id>.dll`(含 LLM 子类)。真实 `compiler_hash` = 该 dll 的 SHA256(不再是 `"c0mp1ler"*8`);build 失败 → `compiler_hash=""` → `CONSTRUCTION_FAILED`(`g3_builder.py:283-291`)。
3. 训练期 gate:在 `<W>/train` 跑一次 G3Real 候选(LEAN config `algorithm-type-name=<LLM 子类名,如 Gold2G3Real_<candidate_id_safe>>`、`algorithm-location=<candidate_dir>/bin/<candidate_id>.dll`),`training_gate_passed` = 训练期 sharpe ≥ `min_dsr=0.0`(与 G1 同款门,不改门槛)。子类名 = `config_hash` 字段的一部分(见 §3.6)。
4. 返回 `GenerationResponse(source_text=<C#源码>, compiler_hash=<dll sha256>, config_hash=<config 含子类名 + algorithm-location>, training_gate_passed=<gate>)` —— G3Builder 已消费此协议(`g3_builder.py:140-155`)。`not gate_passed` → `CANDIDATE_REJECTED` 复用 G2 hash(`g3_builder.py:307-321`)。

### 3.6 algorithm-type-name 透传(给 P4 盲跑)

`G3RealGenerator` 把 LLM 产出的子类名(如 `Gold2G3Real_<candidate_id_safe>`)与编译出的 dll 路径写进 `config_hash` 的输入(一个 JSON:`{"algorithm_type_name":..., "algorithm_location":..., "instrument":...}`)。`_frozen_params_for`(§4.2)对 G3 从此 JSON 读 `algorithm_type_name` + `algorithm_location`,供 P4 盲跑 config 用。`candidate_id_safe` 由 `partition.replace("/","-")`(C1 已建立的净化)保证文件系统安全。

### 3.5 Q-B 修复:regime 非对称 Risk Model

LLM 生成的 `Gold2RegimeAsymmetricRiskModel`(LLM 可内联进 `G3.cs`,或作为 LLM 生成的辅助类)的关键逻辑:

```
当 regime == RISING_FAST 且趋势因子方向向上:
    capFactor = 1.0   # 不封顶,让赢家跑 —— 修牛市踏空
当 drawdown > 阈值 或 extreme_triggered:
    capFactor = 收紧值  # 仅回撤/高波动时收紧 —— 修下跌无保护
否则:
    capFactor = G0 默认
```

这同时修牛市踏空(上涨 regime 不削)与下跌保护(回撤才收紧)。**能放松 cap = 修复成立**(方案 2 的虚函数钩子替换相比方案 1 加性插件的关键能力差异:方案 1 只能加约束不能放松,修不了踏空诊断)。

---

## 4. supersede 语义 + P3/P4 接线

### 4.1 supersede 不 delete

- `_StaticGen`(`construct_p3.py:51-72`)**保留**,作 `GOLD2_G3_GENERATOR=static` env 的 fallback / 原 4-defect 闭环 frozen 实验复现。生产默认 = 新 `G3RealGenerator`。
- `construct_p3.py:238` 的 `G3Builder(_StaticGen(), budget=4)` → 由 `G3RealGenerator()` 替换(可经 env 切回 static)。
- `_REQUIRED_STAGES=("G0","G1","G2","G3")`(`blind_evaluator.py:203`)**不变** —— G3 仍是一个 stage,只是 generator 换成真 LLM。

### 4.2 _resolve_final_stage 升级(核心行为变化)

原 `_resolve_final_stage`(`construct_p4.py:116-128`)返回 `"G2" if win.g2_shaping else "G1" if win.g1_selected else "G0"`,G3 桩不编译必别名回 G2/G1/G0。

升级后:G3 候选**真编译 + gate 通过**时,`_resolve_final_stage` 返回 **"G3"**(非别名)。G3 候选现在可运行 —— G3Real runner 在盲年真起 dotnet。`_frozen_params_for`(`construct_p4.py:98-113`)对 G3 不再返回 None,而是从 frozen G3Real 候选读 `algorithm-type-name`。

> **诚实标注**:这是相对原 4-defect 闭环的行为变化(原 G3 桩不编译必别名)。这是 supersede 的核心:G3 从"桩别名"升级为"真候选"。既有 4-defect 闭环 frozen 证据(`result/gold2-p4-construction/p4_report.json`)**不动**;新跑走新 ev_root。

### 4.3 P4 盲测 ΔSharpe

`aggregate_delta = G3Real盲年sharpe − G0盲年sharpe`,盲年真起 LEAN(`_blind_runner_factory` `construct_p4.py:131-145` 的 runner 签名 `Callable[[FrozenCandidate], Any]` 不变,只是 G3 候选现在可运行)。`candidate_set_sha256` 在 `execute_frozen`(`blind_evaluator.py:526-533`)校验 = frozen hash。

---

## 5. 错误处理

| 错误 | 处理 |
|---|---|
| LLM 不可用 / 超时 | `G3RealGenerator.__call__` 抛 → `G3Builder` catch(`g3_builder.py:198-204`)→ `CONSTRUCTION_FAILED`;该窗口标不可评价(诚实,不伪造) |
| C# 编译失败 | `compiler_hash=""` → `CONSTRUCTION_FAILED`(`g3_builder.py:283-291`) |
| 接口违反(子类没 override BuildRiskModels / 改了其他层) | 编译期(type 可见性)+ 运行期 reflect(确认 override 了 BuildRiskModels 且未 override Initialize 主体)→ `CANDIDATE_REJECTED` |
| blind 后 mutation | `BlindEvaluator.validate_immutability` 已拦(`blind_evaluator.py:519-524`) |
| fallback 到 _StaticGen | env `GOLD2_G3_GENERATOR=static` → 原桩路径(复现旧实验) |
| 诚实标注 | G3-Real 跨 LLM 隔离档 —— 报告明示;真跑后无论 EFFECTIVE/NOT_EFFECTIVE 都诚实 |

---

## 6. 测试矩阵(Python 单测,`Tests/gold2_closed_loop/`)

| 测试 | 覆盖 | 断言 |
|---|---|---|
| `test_g3real_generator_calls_llm_no_blind_leakage` | 反 p-hacking | prompt 字符串不含 blind 年份(2022/2023/2024/2025)/`blind` 关键词 |
| `test_base_class_default_g0_byte_identical` | §1.2 G0 守恒 | 默认 `BuildRiskModels()` 返回的 model 类型+ctor 参数 = `Gold2BetaVolTargetStrategy.cs:106-107` |
| `test_g3real_candidate_compiles` | §3.4 | LLM 产出源码 `dotnet build` 成功 + `compiler_hash` 非空 |
| `test_g3real_interface_violation_rejected` | §5 | 子类未 override BuildRiskModels → `CANDIDATE_REJECTED` |
| `test_g3real_freeze_before_open` | 反 p-hacking | `source_sha256` 在 `blind_opened.json` 之前已算(G3Builder.build 顺序) |
| `test_resolve_final_stage_returns_g3_when_compiled` | §4.2 | G3 候选编译+gate 通过 → `_resolve_final_stage` 返回 "G3"(非别名) |
| `test_multi_instrument_registry_300_50` | §2 | `Gold2InstrumentRegistry.Get("000300")`/`("000688")` 返回有效 spec,基类 Initialize 不抛 |
| `test_forbidden_references_not_matched` | §6 隔离 | 新模块名不匹配 `_FORBIDDEN_REFERENCES` whole-token |
| `test_no_p_hacking_regression` | 反 p-hacking | 网格 `{20,30}×{0.11,0.15}`、budget=4、seed=17、min_dsr=0.0 不变;blind 后无重选 |

---

## 7. 诚实验证门(端到端真跑)

修完后真跑一次完整 P3→P4(每窗口 LLM 生成 + 编译 + gate + 盲年真起 LEAN),产出真实的:
- G3Real 候选是否真编译 + gate 通过(可能失败——LLM 产出不可编译,诚实)
- G3Real 盲年 ΔSharpe vs G0(可能正可能负,真增量)
- regime 非对称修复是否真的修了牛市踏空(W3/W4 ΔSharpe 转正)+ 下跌保护(W1 ΔSharpe 转正)

**无论 EFFECTIVE 还是 NOT_EFFECTIVE,都诚实**。这是跨 LLM 档的诚实机械闭环真谛。

---

## 8. 边界(不做的事,明示)

- **不动 LEAN 核心 C#**(`never-modify-lean-native`)。
- **不改 Gold2BetaVolTargetStrategy 等成熟 feature 既有行为**(`never-modify-existing-features`);C# 改动仅新增 `Gold2ReconstructionCandidateBase` + `Gold2InstrumentSpec` registry + LLM 子类目录,默认中性。
- **不动网格/门槛**:网格 `{20,30}×{0.11,0.15}`、budget=4、seed=17、min_dsr=0.0 不变(反 p-hacking)。
- **不接 production inspiration/evolution_scheduler/layer_state**;新模块独立,注入 runner(§6 隔离)。
- **不修 P3 封存签名的结构性占位**(同原 4-defect 闭环 spec)——属已知诚实限制,报告标注。
- **既有 4-defect 闭环已封存的 frozen 证据不动**(supersede 不 mutate)。
- 沪深300/科创50 的真数据接入(数据源/factor 校准)不在本规格范围;本规格只提供 `Gold2InstrumentSpec` registry 的扩展点与 518880 的完整实现,300/50 的具体 spec 条目在各自后续规格补。

---

## 9. 文件改动清单

| 文件 | 改动 | 缺陷/目的 |
|---|---|---|
| `Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs` | 新建基类 + `BuildRiskModels` 虚函数钩子(默认 G0 行为) | §1 架构 + Q-C 统一 |
| `Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2InstrumentSpec.cs` | 新建 instrument registry | §2 多标的统一 |
| `Scripts/gold2_closed_loop/g3_real_llm_client.py` | 新建 glm-5.2 HTTP 客户端(mirror soloquant,不 import) | §3.3 LLM 契约 |
| `Scripts/gold2_closed_loop/g3_real_generator.py` | 新建 `G3RealGenerator`(调 LLM + 编译 + gate → GenerationResponse) | §3.4 输出→冻结 |
| `Scripts/gold2_closed_loop/g3_real_candidate.csproj.tmpl` | 新建候选编译 .csproj 模板(指向 Algorithm.CSharp.csproj) | §3.4 编译 harness |
| `Scripts/gold2_closed_loop/construct_p3.py:238` | `G3Builder(_StaticGen(),...)` → `G3Builder(G3RealGenerator(),...)`(env 可切回 static) | §4.1 supersede |
| `Scripts/gold2_closed_loop/construct_p4.py:116-128,98-113` | `_resolve_final_stage` G3 编译通过时返回 "G3";`_frozen_params_for` G3 读 algorithm-type-name | §4.2 真候选 |
| `Tests/gold2_closed_loop/`(Python 单测) | 测试矩阵 §6 | 全部 |

**注**:`_StaticGen`(`construct_p3.py:51-72`)**保留**作 fallback / 复现,不删除。

---

*本规格所有断言均可在 worktree `gold2-closed-loop-proof` 内以 file:line 追溯。实施按 writing-plans 产出的计划执行,最终以端到端真跑(§7)验证。跨 spec §6 LLM 隔离档已在 §0/§4.2/§8 诚实标注。*
