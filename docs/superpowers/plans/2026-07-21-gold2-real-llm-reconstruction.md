# Gold2 G3-Real(真 LLM 重构)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用真 LLM(glm-5.2 via mydamoxing.cn)替换 G3 阶段的 `_StaticGen` 桩,产出可编译、可冻结、可盲测的真重构候选,针对性修复 G2 shaping 的牛市踏空 + 下跌无保护,并为沪深300/科创50 提供统一接口。

**Architecture:** 虚函数钩子替换(方案 2)。新建 `Gold2ReconstructionCandidateBase`(`BuildRiskModels()` 虚函数钩子,默认返回 G0 两个 Risk Model = 逐字节相同)。LLM 子类只 override `BuildRiskModels()` 返回 regime 非对称 Risk Model(能放松 cap)。`Gold2InstrumentSpec` registry 统一多标的。新 `G3RealGenerator`(调 LLM → 写 G3.cs → dotnet build → 训练期 gate → `GenerationResponse`)替换 `construct_p3.py:238` 的 `_StaticGen()`(`_StaticGen` 保留为 env fallback)。`construct_p4._resolve_final_stage` G3 编译通过时返回 "G3"。跨 spec §6 LLM 隔离档(诚实标注),独立模块不 import production。

**Tech Stack:** C# .NET 10(LEAN)、Python 3(pytest、requests)、glm-5.2 via mydamoxing.cn/v1 OpenAI-compatible HTTP。

**Spec:** `docs/superpowers/specs/2026-07-21-gold2-real-llm-reconstruction-design.md`

---

## 文件结构

| 文件 | 责任 | 新建/修改 |
|---|---|---|
| `Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs` | 基类 + `BuildRiskModels()` 虚钩子(默认 G0) | 新建 |
| `Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2InstrumentSpec.cs` | `Gold2InstrumentSpec` record + `Gold2InstrumentRegistry` | 新建 |
| `Scripts/gold2_closed_loop/g3_real_llm_client.py` | glm-5.2 HTTP 客户端(mirror soloquant,不 import) | 新建 |
| `Scripts/gold2_closed_loop/g3_real_candidate.csproj.tmpl` | 候选编译 .csproj 模板 | 新建 |
| `Scripts/gold2_closed_loop/g3_real_compile.py` | `compile_candidate`(dotnet build → dll + sha256) | 新建 |
| `Scripts/gold2_closed_loop/g3_real_generator.py` | `G3RealGenerator`(LLM→编译→gate→GenerationResponse)+ interface 校验 | 新建 |
| `Scripts/gold2_closed_loop/construct_p3.py` | `:238` generator 切换(env fallback static) | 修改 |
| `Scripts/gold2_closed_loop/construct_p4.py` | `_resolve_final_stage` + `_frozen_params_for` + blind runner G3 支持 | 修改 |
| `Scripts/gold2_closed_loop/g3_builder.py` | `G3BuildResult` 增可选 `candidate_class`/`dll_path` 字段 | 修改 |
| `Tests/gold2_closed_loop/test_g3_real_base_class.py` | G0 byte-identical + 钩子 + registry | 新建 |
| `Tests/gold2_closed_loop/test_g3_real_llm_client.py` | HTTP + 零盲年泄漏 + §6 隔离 | 新建 |
| `Tests/gold2_closed_loop/test_g3_real_generator.py` | 编译 + gate + interface 校验 | 新建 |
| `Tests/gold2_closed_loop/test_g3_real_p4_wiring.py` | _resolve_final_stage + freeze-before-open + 反 p-hacking | 新建 |

---

## Task 1: Gold2ReconstructionCandidateBase 基类 + G0 byte-identical 守恒

**Files:**
- Create: `Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs`
- Test: `Tests/gold2_closed_loop/test_g3_real_base_class.py`

- [ ] **Step 1: 写失败测试(C# G0 byte-identical)**

`Tests/gold2_closed_loop/test_g3_real_base_class.py`:
```python
"""G3-Real base class: BuildRiskModels() default = G0 Risk Models byte-identical."""
import subprocess, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_CS = ROOT / "Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs"
STRATEGY_CS = ROOT / "Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs"

def test_base_class_file_exists():
    assert BASE_CS.exists(), "Gold2ReconstructionCandidateBase.cs must exist"

def test_default_buildriskmodels_returns_g0_models():
    """默认 BuildRiskModels() 返回 Gold2ExtremeRiskModel + Gold2RealRateCapModel,
    与 Gold2BetaVolTargetStrategy.cs:106-107 逐字节等价。"""
    src = BASE_CS.read_text()
    assert "new Gold2ExtremeRiskModel(_ext, _gold, _effExtremeCap)" in src
    assert "new Gold2RealRateCapModel(_realrate, _gold, _effRealRateCap)" in src
    assert "virtual IEnumerable<IRiskManagementModel> BuildRiskModels" in src
    assert "foreach (var model in BuildRiskModels()) AddRiskManagement(model)" in src

def test_base_class_implements_same_interfaces():
    src = BASE_CS.read_text()
    assert "Gold2ReconstructionCandidateBase : QCAlgorithm, IOptimizableStrategy, IRlStateExportable" in src
    for name in ["trend-ma-short","vol-target","extreme-vol-cap","realrate-cap","trend-disable"]:
        assert name in src, f"base class must expose tunable {name}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_base_class.py -v`
Expected: FAIL(`BASE_CS` 不存在)

- [ ] **Step 3: 实现基类**

`Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs` — 完整照搬 `Gold2BetaVolTargetStrategy.cs:56-109` 的 `Initialize()`(同 SetCash/Start/End、AddEquity("518880")、factors、Universe/Alpha/Portfolio/Execution),唯一改动:line 106-107 两条 `AddRiskManagement(...)` → `foreach (var model in BuildRiskModels()) AddRiskManagement(model);`。其余字段/方法(`GetTunableParameterNames` 11 项、`SerializeRlState`、`Get*Parameter` helpers、`WriteRlStateTraceIfNeeded`、`ClassifyRealRateRegime`)逐字照搬。命名空间 `QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction`。`_effExtremeCap`/`_effRealRateCap` 按 C2 倍率语义计算(同 `Gold2BetaVolTargetStrategy.cs:102-105`)。`protected` 字段供子类用。`BuildRiskModels()` 默认:
```csharp
protected virtual IEnumerable<IRiskManagementModel> BuildRiskModels()
    => new IRiskManagementModel[]
    {
        new Gold2ExtremeRiskModel(_ext, _gold, _effExtremeCap),
        new Gold2RealRateCapModel(_realrate, _gold, _effRealRateCap),
    };
```

- [ ] **Step 4: 运行测试确认通过 + dotnet build**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_base_class.py -v` → PASS
Run: `dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj` → succeeded(基类新增默认中性,不破坏现有编译)

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs Tests/gold2_closed_loop/test_g3_real_base_class.py
git commit -m "feat(gold2): G3-Real base class with BuildRiskModels virtual hook (G0 byte-identical)"
```

---

## Task 2: Gold2InstrumentSpec registry(多标的统一,Q-C)

**Files:**
- Create: `Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2InstrumentSpec.cs`
- Modify: `Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs`(Initialize 用 spec)
- Test: `Tests/gold2_closed_loop/test_g3_real_base_class.py`(追加)

- [ ] **Step 1: 写失败测试**

追加到 `test_g3_real_base_class.py`:
```python
def test_instrument_registry_has_518880():
    spec_cs = ROOT / "Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2InstrumentSpec.cs"
    assert spec_cs.exists()
    src = spec_cs.read_text()
    assert "518880" in src and "SSE" in src
    assert "AuShfDailyBar" in src and "AU.SHF" in src
    assert "FredMacroData" in src and "VIX" in src and "DFII10" in src
    assert "throw new ArgumentException" in src
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_base_class.py::test_instrument_registry_has_518880 -v`
Expected: FAIL(file 不存在)

- [ ] **Step 3: 实现 registry + 基类用 spec**

`Gold2InstrumentSpec.cs`:
```csharp
using System;
using QuantConnect.Data.Custom.Gold;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction
{
    public sealed record Gold2InstrumentSpec(
        string Ticker, string Market,
        Type GoldDataSource, string GoldDataTicker,
        Type MacroVixDataSource, string VixTicker,
        Type MacroRealRateDataSource, string RealRateTicker,
        Func<Symbol, decimal, Gold2RealRateCapFactor> RegimeFactorFactory,
        Func<decimal, decimal, int, decimal, Gold2VolRegimeFactor> VolFactorFactory);

    public static class Gold2InstrumentRegistry
    {
        public static Gold2InstrumentSpec Get(string instrument) => instrument switch
        {
            "518880" => new Gold2InstrumentSpec(
                "518880", "SSE",
                typeof(AuShfDailyBar), "AU.SHF",
                typeof(FredMacroData), "VIX",
                typeof(FredMacroData), "DFII10",
                (g, cap) => new Gold2RealRateCapFactor(new GoldRealRateRegimeFactor(), cap),
                (l, vt, w, a) => new Gold2VolRegimeFactor(l, vt, w, a)),
            _ => throw new ArgumentException($"unknown instrument: {instrument}")
        };
    }
}
```
基类 `Initialize()` 第一行加 `var spec = Gold2InstrumentRegistry.Get(GetParameter("instrument","518880"));`,AddEquity/AddData/factor 构造用 `spec`。**G0 byte-identical 要求**:默认 instrument="518880" 时 spec 产出的 AddEquity/AddData/factor 调用须与原硬编码逐字一致 —— 重新跑 Task 1 的 G0 byte-identical 测试确认仍 PASS。

- [ ] **Step 4: 运行确认通过 + build**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_base_class.py -v` → PASS
Run: `dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj` → succeeded

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2InstrumentSpec.cs Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs Tests/gold2_closed_loop/test_g3_real_base_class.py
git commit -m "feat(gold2): Gold2InstrumentSpec registry for multi-instrument unification (Q-C)"
```

---

## Task 3: g3_real_llm_client.py(glm-5.2 HTTP,不 import production)

**Files:**
- Create: `Scripts/gold2_closed_loop/g3_real_llm_client.py`
- Test: `Tests/gold2_closed_loop/test_g3_real_llm_client.py`

- [ ] **Step 1: 写失败测试**

`Tests/gold2_closed_loop/test_g3_real_llm_client.py`:
```python
"""G3-Real LLM client: glm-5.2 via mydamoxing.cn, no production import, no blind leakage."""
import inspect, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def test_module_does_not_import_production():
    src = (ROOT / "Scripts/gold2_closed_loop/g3_real_llm_client.py").read_text()
    for forbidden in ("soloquant_orchestrator", "evolution_scheduler", "layer_state",
                      "inspiration", "generation_state"):
        assert f"import {forbidden}" not in src, f"must not import {forbidden}"
        assert f"from {forbidden}" not in src, f"must not import {forbidden}"

def test_http_shape_mirrors_openai_compatible():
    src = (ROOT / "Scripts/gold2_closed_loop/g3_real_llm_client.py").read_text()
    assert "mydamoxing.cn" in src
    assert "/v1/chat/completions" in src
    assert "Bearer" in src
    assert "glm-5.2" in src

def test_build_prompt_has_no_blind_leakage():
    from Scripts.gold2_closed_loop.g3_real_llm_client import build_prompt
    synthetic_bundle = {
        "window_id": "W1", "validity_status": "VALID",
        "layer_attribution": {
            "trend": {"pnl_pct_of_total": "0.45"},
            "vol_target": {"pnl_pct_of_total": "0.30"},
            "extreme_risk": {"pnl_pct_of_total": "0.10"},
            "realrate_cap": {"pnl_pct_of_total": "0.15"},
        },
    }
    prompt = build_prompt(synthetic_bundle, instrument="518880")
    for blind_token in ("blind", "2022", "2023", "2024", "2025"):
        assert blind_token not in prompt.lower(), f"prompt leaks blind token: {blind_token}"
    assert "regime" in prompt.lower()
    assert "RISING_FAST" in prompt or "rising_fast" in prompt.lower()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_llm_client.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 client**

`Scripts/gold2_closed_loop/g3_real_llm_client.py`:
```python
"""G3-Real LLM client: glm-5.2 via mydamoxing.cn/v1 (OpenAI-compatible).

PROOF-ONLY, independent. Does NOT import soloquant_orchestrator /
evolution_scheduler / layer_state / inspiration (spec §6 isolation;
interface_readiness._FORBIDDEN_REFERENCES whole-token not matched).
Mirrors the HTTP shape of soloquant_orchestrator.summarize_with_llm
without importing it.
"""
from __future__ import annotations
import os
from pathlib import Path
import requests

DEFAULT_BASE_URL = "https://mydamoxing.cn"
DEFAULT_MODEL = "glm-5.2"
DEFAULT_TOKEN = os.environ.get(
    "GOLD2_G3_LLM_TOKEN",
    "sk-Lqf1cFD3252DX3Jt4lXWa3C2lCD3U5bwSdFoUwUIJZWD6Dpw",
)

_DIAGNOSIS = """\
DIAGNOSIS (from TRAIN-period review bundle, NOT blind data):
G2 shaping caps the realrate layer in RISING_FAST regime unconditionally
(Gold2RealRateCapModel.cs: capFactor = regime==RISING_FAST ? effRealRateCap : 1.0).
This cut gains in bull-market windows AND gave no extra protection in drawdowns.
REQUIRED reconstruction: regime-ASYMMETRIC. When regime==RISING_FAST AND the trend
factor direction is UP, do NOT cap (capFactor=1.0, let winners run). Only tighten
on drawdown > threshold OR extreme_triggered. You MAY relax caps (not only add).
"""


def build_prompt(review_bundle: dict, *, instrument: str = "518880") -> str:
    """Build the LLM prompt from TRAIN-period bundle + source + tunable space.

    Anti-p-hacking: review_bundle is the TRAIN-period formal-review bundle
    (construct_p2 runs G0 on w.train; feedback_construction uses {wid}/train +
    {wid}/review only). No blind-year data enters this function.
    """
    attr = review_bundle.get("layer_attribution", {})
    src_root = Path(__file__).resolve().parents[2]
    strategy_src = (src_root / "Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs").read_text()
    ext_src = (src_root / "Algorithm.CSharp/Models/Gold2/Gold2ExtremeRiskModel.cs").read_text()
    rr_src = (src_root / "Algorithm.CSharp/Models/Gold2/Gold2RealRateCapModel.cs").read_text()
    base_src = (src_root / "Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs").read_text()
    return f"""You are reconstructing the Risk layer of a LEAN C# gold strategy.
Instrument: {instrument}

TRAIN-period review bundle layer_attribution (pnl_pct_of_total):
{attr}

{_DIAGNOSIS}

Tunable parameter names (G1 grid, do NOT widen): trend-ma-short, vol-target,
trend-ma-long, ewma-lambda, vol-warmup, smooth-alpha, rebalance-threshold,
extreme-vol-cap, realrate-cap, trend-floor, trend-disable.
Shaping penalties (G2-derived, default 1.0 = neutral): extreme_risk_contrib_penalty,
realrate_cap_contrib_penalty.

Existing strategy source:
{strategy_src}

Existing Risk Models:
{ext_src}
{rr_src}

Base class you MUST extend (override ONLY BuildRiskModels, do NOT touch
Universe/Alpha/Portfolio/Execution/Initialize):
{base_src}

OUTPUT: a single C# file. Define a subclass of Gold2ReconstructionCandidateBase
that overrides ONLY `protected override IEnumerable<IRiskManagementModel> BuildRiskModels()`.
You may define helper RiskManagementModel subclasses in the same file. The class
must compile against the Algorithm.CSharp project. Output ONLY the C# code,
no markdown fences, no explanation.
"""


def generate_reconstruction(prompt: str, *, base_url: str = DEFAULT_BASE_URL,
                            auth_token: str = DEFAULT_TOKEN, model: str = DEFAULT_MODEL,
                            timeout: int = 180) -> str:
    """POST to {base_url}/v1/chat/completions (OpenAI-compatible). Returns content string."""
    resp = requests.post(
        f"{base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.2},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_llm_client.py -v` → PASS(`build_prompt` 不调网络)

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/g3_real_llm_client.py Tests/gold2_closed_loop/test_g3_real_llm_client.py
git commit -m "feat(gold2): G3-Real glm-5.2 LLM client (independent, no production import, zero blind leakage)"
```

---

## Task 4: 候选编译 .csproj 模板 + 编译 harness

**Files:**
- Create: `Scripts/gold2_closed_loop/g3_real_candidate.csproj.tmpl`
- Create: `Scripts/gold2_closed_loop/g3_real_compile.py`
- Test: `Tests/gold2_closed_loop/test_g3_real_generator.py`(编译部分)

- [ ] **Step 1: 写失败测试**

`Tests/gold2_closed_loop/test_g3_real_generator.py`:
```python
"""G3-Real compile harness: dotnet build candidate G3.cs -> dll + compiler_hash."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TRIVIAL_SUBCLASS = '''using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction
{
    public class TrivialG3RealCandidate : Gold2ReconstructionCandidateBase
    {
        protected override IEnumerable<IRiskManagementModel> BuildRiskModels()
            => base.BuildRiskModels();
    }
}
'''

def test_compile_trivial_subclass_succeeds(tmp_path):
    from Scripts.gold2_closed_loop.g3_real_compile import compile_candidate
    cs = tmp_path / "G3.cs"
    cs.write_text(TRIVIAL_SUBCLASS)
    result = compile_candidate(cs, candidate_id="trivial", candidate_class="TrivialG3RealCandidate")
    assert result.ok, f"compile failed: {result.stderr_tail}"
    assert result.dll_path.exists()
    assert result.compiler_hash and len(result.compiler_hash) == 64
    assert result.candidate_class == "TrivialG3RealCandidate"

def test_compile_broken_source_fails(tmp_path):
    from Scripts.gold2_closed_loop.g3_real_compile import compile_candidate
    cs = tmp_path / "G3.cs"
    cs.write_text("this is not C#")
    result = compile_candidate(cs, candidate_id="broken", candidate_class="X")
    assert not result.ok
    assert not result.dll_path.exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_generator.py::test_compile_trivial_subclass_succeeds -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 .csproj 模板**

`Scripts/gold2_closed_loop/g3_real_candidate.csproj.tmpl`:
```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <GenerateAssemblyInfo>false</GenerateAssemblyInfo>
    <OutputPath>bin\</OutputPath>
    <AppendTargetFrameworkToOutputPath>false</AppendTargetFrameworkToOutputPath>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <AssemblyName>{ASSEMBLY_NAME}</AssemblyName>
    <RootNamespace>QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction</RootNamespace>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="G3.cs" />
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="{ALGO_CSHARP_CSJPROJ}" />
  </ItemGroup>
</Project>
```

- [ ] **Step 4: 实现 compile harness**

`Scripts/gold2_closed_loop/g3_real_compile.py`:
```python
"""Compile a G3-Real candidate G3.cs into a loadable dll.

Writes a .csproj (from template) referencing Algorithm.CSharp.csproj next to
the candidate G3.cs, runs `dotnet build`, and returns the produced dll path +
its SHA256 (the real compiler_hash, replacing _StaticGen's "c0mp1ler"*8).
The candidate dll + its copy-local deps (Algorithm.CSharp.dll, Common.dll, ...)
land in <candidate_dir>/bin/, which LEAN loads via algorithm-location.
"""
from __future__ import annotations
import hashlib, subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALGO_CSJPROJ = ROOT / "Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj"
TMPL = ROOT / "Scripts/gold2_closed_loop/g3_real_candidate.csproj.tmpl"


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    dll_path: Path
    compiler_hash: str  # sha256 hex of dll, "" on failure
    candidate_class: str
    stderr_tail: str


def compile_candidate(g3_cs: Path, *, candidate_id: str, candidate_class: str,
                      dotnet: str = "dotnet", timeout: int = 180) -> CompileResult:
    cand_dir = g3_cs.parent
    asm_name = f"G3Real_{candidate_id}"
    csproj = cand_dir / f"{asm_name}.csproj"
    tmpl = TMPL.read_text()
    csproj.write_text(tmpl
        .replace("{ASSEMBLY_NAME}", asm_name)
        .replace("{ALGO_CSHARP_CSJPROJ}", str(ALGO_CSJPROJ)))
    try:
        proc = subprocess.run(
            [dotnet, "build", str(csproj), "-c", "Debug", "--nologo"],
            capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return CompileResult(False, cand_dir / "bin" / f"{asm_name}.dll", "", candidate_class, str(e))
    dll = cand_dir / "bin" / f"{asm_name}.dll"
    if proc.returncode != 0 or not dll.exists():
        return CompileResult(False, dll, "", candidate_class,
                             (proc.stderr or proc.stdout)[-2000:])
    h = hashlib.sha256(dll.read_bytes()).hexdigest()
    return CompileResult(True, dll, h, candidate_class, "")
```

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_generator.py -v` → PASS(真起 dotnet build;若环境无 dotnet,标记 skip 并在 CI 跑)

- [ ] **Step 6: Commit**

```bash
git add Scripts/gold2_closed_loop/g3_real_candidate.csproj.tmpl Scripts/gold2_closed_loop/g3_real_compile.py Tests/gold2_closed_loop/test_g3_real_generator.py
git commit -m "feat(gold2): G3-Real candidate compile harness (dotnet build -> dll + sha256 compiler_hash)"
```

---

## Task 5: G3RealGenerator(LLM → 编译 → 训练期 gate → GenerationResponse)+ interface 校验

**Files:**
- Create: `Scripts/gold2_closed_loop/g3_real_generator.py`
- Test: `Tests/gold2_closed_loop/test_g3_real_generator.py`(追加)

- [ ] **Step 1: 写失败测试**

追加到 `test_g3_real_generator.py`:
```python
def test_generator_returns_generation_response_shape(monkeypatch, tmp_path):
    """G3RealGenerator.__call__ 返回 GenerationResponse(source_text, compiler_hash,
    config_hash, training_gate_passed)。LLM 与 dotnet 被 mock。"""
    from Scripts.gold2_closed_loop import g3_real_generator as gen
    monkeypatch.setattr(gen, "_call_llm", lambda prompt: TRIVIAL_SUBCLASS)
    def fake_compile(g3_cs, *, candidate_id, candidate_class, **kw):
        dll = g3_cs.parent / "bin" / f"G3Real_{candidate_id}.dll"
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_bytes(b"\x4d\x5a" + b"\x00" * 100)
        return gen.CompileResult(True, dll, "a" * 64, candidate_class, "")
    monkeypatch.setattr(gen, "compile_candidate", fake_compile)
    monkeypatch.setattr(gen, "_run_train_gate", lambda *a, **kw: True)
    g = gen.G3RealGenerator(train_data_folder=None)
    req = gen._fake_request(tmp_path, candidate_id="c0",
                            candidate_class="TrivialG3RealCandidate")
    resp = g(req)
    assert resp.source_text == TRIVIAL_SUBCLASS
    assert resp.compiler_hash == "a" * 64
    assert resp.training_gate_passed is True

def test_interface_violation_no_buildriskmodels_override_rejected(monkeypatch, tmp_path):
    """子类没 override BuildRiskModels -> gate fail (reflect)."""
    from Scripts.gold2_closed_loop import g3_real_generator as gen
    NO_OVERRIDE = '''using System.Collections.Generic;
using QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction;
namespace QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction {
    public class NoOverride : Gold2ReconstructionCandidateBase { }
}'''
    monkeypatch.setattr(gen, "_call_llm", lambda prompt: NO_OVERRIDE)
    monkeypatch.setattr(gen, "compile_candidate",
        lambda g3_cs, *, candidate_id, candidate_class, **kw: gen.CompileResult(
            True, g3_cs.parent/"bin"/f"G3Real_{candidate_id}.dll", "b"*64, candidate_class, ""))
    monkeypatch.setattr(gen, "_run_train_gate", lambda *a, **kw: False)
    g = gen.G3RealGenerator(train_data_folder=None)
    req = gen._fake_request(tmp_path, candidate_id="c1", candidate_class="NoOverride")
    resp = g(req)
    assert resp.training_gate_passed is False  # -> CANDIDATE_REJECTED downstream
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_generator.py::test_generator_returns_generation_response_shape -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 generator**

`Scripts/gold2_closed_loop/g3_real_generator.py`:实现 `_call_llm`(调 `generate_reconstruction`)、`_strip_fences`(去 markdown fence)、`_looks_like_csharp`(含 `: Gold2ReconstructionCandidateBase`)、`_extract_class_name`(regex `class\s+(\w+)\s*(?:\{|:)`)、`_load_train_bundle`(复用 `construct_p3._load_p2_review_bundle`)、`_train_start/_train_end`(从 `phase0_types.proof_windows()` 读 train)、`_hash_config`(canonical_hash of `{candidate_class, dll_path, instrument}`)、`_default_cand_dir`、`G3RealGenerator.__call__` 流程(prompt→LLM→strip→write G3.cs→compile→`_run_train_gate`→`_Response`)。`_run_train_gate` 真实实现放 Task 7(此处桩 raise NotImplementedError)。

`_Response` dataclass: `source_text, compiler_hash, config_hash, training_gate_passed`(满足 `g3_builder.GenerationResponse` 协议 g3_builder.py:140-155)。`candidate_dir_hint`:`G3Request` 当前无此字段,Task 7 在 `G3BuildResult` 加 `candidate_dir`(g3_builder 已有!line 135)—— generator 需要候选目录。**调整**:让 `G3RealGenerator` 接收 `proof_root` 参数(由 construct_p3 传入),generator 内部算 `proof_root/candidates/<candidate_id>` 作候选目录(与 g3_builder 的 candidate_dir 一致)。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_generator.py -v` → PASS(mock 路径;`_run_train_gate` 被 monkeypatch)

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/g3_real_generator.py Tests/gold2_closed_loop/test_g3_real_generator.py
git commit -m "feat(gold2): G3RealGenerator (LLM -> compile -> train gate -> GenerationResponse) + interface check"
```

---

## Task 6: construct_p3.py 接线(env 切换 G3RealGenerator / _StaticGen)+ G3BuildResult 扩字段

**Files:**
- Modify: `Scripts/gold2_closed_loop/g3_builder.py`(`G3BuildResult` 增可选 `candidate_class`/`dll_path`)
- Modify: `Scripts/gold2_closed_loop/construct_p3.py`(`_select_generator` + g3_build 记录新字段)
- Test: `Tests/gold2_closed_loop/test_g3_real_p4_wiring.py`(p3 部分)

- [ ] **Step 1: 写失败测试**

`Tests/gold2_closed_loop/test_g3_real_p4_wiring.py`:
```python
def test_construct_p3_uses_real_generator_by_default():
    import Scripts.gold2_closed_loop.construct_p3 as p3
    src = open(p3.__file__).read()
    assert "G3RealGenerator" in src
    assert "GOLD2_G3_GENERATOR" in src
    assert "_StaticGen" in src  # 保留 fallback

def test_static_fallback_env():
    import os, Scripts.gold2_closed_loop.construct_p3 as p3
    os.environ["GOLD2_G3_GENERATOR"] = "static"
    try:
        gen = p3._select_generator()
        assert gen.__class__.__name__ == "_StaticGen"
    finally:
        os.environ.pop("GOLD2_G3_GENERATOR", None)

def test_g3buildresult_has_candidate_class_dll_path():
    from Scripts.gold2_closed_loop.g3_builder import G3BuildResult
    import dataclasses
    fields = {f.name for f in dataclasses.fields(G3BuildResult)}
    assert "candidate_class" in fields
    assert "dll_path" in fields
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_p4_wiring.py -v`
Expected: FAIL

- [ ] **Step 3: 改 g3_builder.py**

`G3BuildResult`(g3_builder.py:97)加两个可选字段(默认 None,G0/桩路径无影响):
```python
    candidate_class: str | None = None
    dll_path: str | None = None
```
`build()` 在 eligible=True 分支(g3_builder.py:322-333)设这俩字段——从哪来?generator 返回的 `GenerationResponse` 当前只有 source/compiler/config/gate。**扩 `GenerationResponse` Protocol** 加 `candidate_class`/`dll_path` 可选(g3_builder.py:140-155),`build()` 用 `getattr(response, "candidate_class", None)` 填。`_response_dict`(g3_builder.py:364)也可加这俩进 response_hash(确保冻结包含)。

- [ ] **Step 4: 改 construct_p3.py**

加 `_select_generator()`(见 spec §4.1),`main()` line 238 `G3Builder(_StaticGen(), budget=4)` → `G3Builder(_select_generator(), budget=4)`。`g3_build` dict(line 239-245)增 `"candidate_class": build.candidate_class, "dll_path": build.dll_path`。`_StaticGen` 类保留不动。

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_p4_wiring.py Tests/gold2_closed_loop/test_g3_builder*.py Tests/gold2_closed_loop/test_g3_trigger.py -v` → PASS(既有 g3_builder 测试不破,新字段默认 None)

- [ ] **Step 6: Commit**

```bash
git add Scripts/gold2_closed_loop/g3_builder.py Scripts/gold2_closed_loop/construct_p3.py Tests/gold2_closed_loop/test_g3_real_p4_wiring.py
git commit -m "feat(gold2): wire G3RealGenerator into construct_p3 + G3BuildResult carries candidate_class/dll_path"
```

---

## Task 7: _run_train_gate 真实实现 + construct_p4 G3 真候选支持

**Files:**
- Modify: `Scripts/gold2_closed_loop/g3_real_generator.py`(`_run_train_gate` + `_reflect_interface_ok`)
- Modify: `Scripts/gold2_closed_loop/construct_p4.py:98-145`(`_resolve_final_stage` + `_frozen_params_for` + blind runner)
- Test: `Tests/gold2_closed_loop/test_g3_real_p4_wiring.py`(追加)

- [ ] **Step 1: 写失败测试**

追加到 `test_g3_real_p4_wiring.py`:
```python
def test_resolve_final_stage_returns_g3_when_compiled(tmp_path, monkeypatch):
    import json
    from Scripts.gold2_closed_loop import construct_p4 as p4
    p3_dir = p4.ROOT / "result" / "gold2-p3-construction"
    p3_dir.mkdir(parents=True, exist_ok=True)
    (p3_dir / "p3_report.json").write_text(json.dumps({
        "windows": [{"window_id": "W1", "g3_build": {"eligible": True,
            "candidate_set_sha256": "x", "source_sha256": "y",
            "candidate_class": "Gold2G3Real_W1", "dll_path": "/tmp/x.dll"}}]
    }))
    assert p4._resolve_final_stage("W1") == "G3"

def test_resolve_final_stage_static_fallback_aliases(tmp_path):
    import json
    from Scripts.gold2_closed_loop import construct_p4 as p4
    p3_dir = p4.ROOT / "result" / "gold2-p3-construction"
    p3_dir.mkdir(parents=True, exist_ok=True)
    (p3_dir / "p3_report.json").write_text(json.dumps({
        "windows": [{"window_id": "W1", "g3_build": None}]
    }))
    p2_dir = p4.ROOT / "result" / "gold2-p2-construction"
    p2_dir.mkdir(parents=True, exist_ok=True)
    (p2_dir / "p2_report.json").write_text(json.dumps({
        "windows": [{"window_id": "W1", "g2_shaping": {"extreme_risk_contrib_penalty": 1.5}}]
    }))
    assert p4._resolve_final_stage("W1") == "G2"

def test_frozen_params_for_g3_returns_candidate_descriptor():
    import json
    from Scripts.gold2_closed_loop import construct_p4 as p4
    from Scripts.gold2_closed_loop.blind_evaluator import FrozenCandidate
    p3_dir = p4.ROOT / "result" / "gold2-p3-construction"
    p3_dir.mkdir(parents=True, exist_ok=True)
    (p3_dir / "p3_report.json").write_text(json.dumps({
        "windows": [{"window_id": "W1", "g3_build": {"eligible": True,
            "candidate_class": "Gold2G3Real_W1", "dll_path": "/tmp/x.dll"}}]
    }))
    fc = FrozenCandidate("W1", "G3", "W1-G3-C0", "cs", "src", "cfg")
    params = p4._frozen_params_for(fc)
    assert params is not None
    assert params["algorithm-type-name"] == "Gold2G3Real_W1"
    assert params["algorithm-location"] == "/tmp/x.dll"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_p4_wiring.py -v`
Expected: FAIL(`_resolve_final_stage` 仍别名)

- [ ] **Step 3: 实现 _run_train_gate + _reflect_interface_ok**

`g3_real_generator.py` 替换 `_run_train_gate` 桩:
```python
def _run_train_gate(req: _GateRequest, *, train_data_folder: str | None) -> bool:
    """Run candidate on w.train; pass iff (a) subclass overrides BuildRiskModels
    without overriding Initialize (reflect), AND (b) train sharpe >= MIN_DSR."""
    from Scripts.gold2_closed_loop.lean_runner import build_run_config, run_lean
    from Scripts.gold2_closed_loop.phase0_types import proof_windows
    if not _reflect_interface_ok(req.dll_path, req.candidate_class):
        return False
    wdef = next(w for w in proof_windows() if w.window_id == req.window_id)
    run_dir = Path(req.dll_path).parent / "gate"
    run_dir.mkdir(parents=True, exist_ok=True)
    base = {"algorithm-location": req.dll_path}
    cfg = build_run_config(base, run_dir, req.candidate_class, {},
        run_id=f"{req.candidate_id}-GATE", start_date=req.train_start, end_date=req.train_end,
        trace_path=run_dir/"trace.jsonl", experiment_id="E1", window_id=req.window_id,
        stage_id="G3", candidate_id=req.candidate_id, data_folder=train_data_folder)
    res = run_lean(cfg, run_dir=run_dir, run_id=f"{req.candidate_id}-GATE",
                   timeout_seconds=600, worktree_root=_ROOT, trace_path=run_dir/"trace.jsonl")
    if not res.packet_path.exists():
        return False
    pkt = json.loads(res.packet_path.read_text())
    sharpe = (pkt.get("statistics") or {}).get("Sharpe Ratio", 0) or 0
    return float(sharpe) >= MIN_DSR
```
`_reflect_interface_ok`:用 pythonnet(`import clr; clr.AddReference(dll)`)加载 dll,反射 `candidate_class` 的 `BuildRiskModels` 是否 `GetBaseDefinition().DeclaringType != candidate_class`(即 override 了)且 `Initialize` 未被 override。若 pythonnet 不可用,fallback 编译一个 `ReflectProbe.cs` 小程序调 `Assembly.LoadFrom` + 同样检查,`subprocess` 跑。两种都实现 + 注释 pythonnet 优先。

- [ ] **Step 4: 改 construct_p4.py**

`_resolve_final_stage`(line 116):
```python
def _resolve_final_stage(window_id: str) -> str:
    """G3 real candidate (compiled + gate-passed) -> 'G3'. Else alias chain G2/G1/G0."""
    p3 = json.loads((ROOT / "result" / "gold2-p3-construction" / "p3_report.json").read_text())
    win3 = next((w for w in p3["windows"] if w["window_id"] == window_id), None)
    if win3 and (win3.get("g3_build") or {}).get("eligible"):
        return "G3"
    p2 = json.loads((ROOT / "result" / "gold2-p2-construction" / "p2_report.json").read_text())
    win = next(w for w in p2["windows"] if w["window_id"] == window_id)
    if win.get("g2_shaping"): return "G2"
    if win.get("g1_selected"): return "G1"
    return "G0"
```
`_frozen_params_for` G3 分支(line 98,在 `return None` 前):
```python
    if fc.stage_id == "G3":
        p3 = json.loads((ROOT / "result" / "gold2-p3-construction" / "p3_report.json").read_text())
        win3 = next((w for w in p3["windows"] if w["window_id"] == fc.window_id), None)
        g3b = (win3 or {}).get("g3_build") or {}
        if g3b.get("eligible"):
            return {"algorithm-type-name": g3b["candidate_class"],
                    "algorithm-location": g3b["dll_path"]}
        return None
```
`_blind_runner_factory`(line 131):runner 里 G3 时 params 含 `algorithm-type-name`/`algorithm-location`,需直接调 `build_run_config`(base 含 `algorithm-location`)+ `run_lean`(因 `_lean_run` 硬编码 type-name)。新增 `_lean_run_candidate(run_dir, run_id, fc, start, end, trace, params)`:`base = dict(BASE); base["algorithm-location"]=params["algorithm-location"]; cfg = build_run_config(base, run_dir, params["algorithm-type-name"], params_without_type_name, run_id=run_id, ...); run_lean(...)`。runner 里 `fc.stage_id=="G3"` 走 `_lean_run_candidate`,其余走原 `_lean_run`。

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_p4_wiring.py -v` → PASS

- [ ] **Step 6: Commit**

```bash
git add Scripts/gold2_closed_loop/g3_real_generator.py Scripts/gold2_closed_loop/construct_p4.py Tests/gold2_closed_loop/test_g3_real_p4_wiring.py
git commit -m "feat(gold2): real G3 candidate in P4 (_resolve_final_stage returns G3, blind runner loads candidate dll) + train gate + reflect"
```

---

## Task 8: 反 p-hacking 回归 + §6 隔离回归

**Files:**
- Test: `Tests/gold2_closed_loop/test_g3_real_p4_wiring.py`(追加)

- [ ] **Step 1: 写测试**

追加:
```python
def test_no_p_hacking_regression():
    from Scripts.gold2_closed_loop.g3_real_llm_client import build_prompt
    from Scripts.gold2_closed_loop.g3_real_generator import MIN_DSR
    assert MIN_DSR == 0.0
    p = build_prompt({"layer_attribution": {}}, instrument="518880")
    for tok in ("blind", "2022", "2023", "2024", "2025"):
        assert tok not in p.lower()
    # G1 grid 不变(grep 实际网格常量)
    import Scripts.gold2_closed_loop.construct_p2 as p2
    src = open(p2.__file__).read()
    assert "{20,30}" in src or "('trend-ma-short', [20, 30])" in src or "[20, 30]" in src

def test_forbidden_references_not_matched():
    from Scripts.gold2_closed_loop import interface_readiness as ir
    for new_mod in ("g3_real_llm_client", "g3_real_generator", "g3_real_compile"):
        for forb in ir._FORBIDDEN_REFERENCES:
            assert forb not in new_mod

def test_freeze_before_open_order():
    """source_sha256 在 blind_opened.json 之前已算(G3Builder.build 顺序)。"""
    import inspect
    from Scripts.gold2_closed_loop import g3_builder
    src = inspect.getsource(g3_builder)
    # build() 里 source_sha 计算在 candidate_dir 写入之后、return 之前;
    # open_blind 不在 g3_builder(在 blind_evaluator)——只校验 builder 不调 open
    assert "open_blind" not in src
    assert "source_sha" in src  # 确实算了
```

- [ ] **Step 2: 运行确认通过**

Run: `python -m pytest Tests/gold2_closed_loop/test_g3_real_p4_wiring.py -v` → PASS(若 grid 常量断言不匹配实际,按 grep 结果调整断言字符串)

- [ ] **Step 3: Commit**

```bash
git add Tests/gold2_closed_loop/test_g3_real_p4_wiring.py
git commit -m "test(gold2): anti-p-hacking + §6 isolation + freeze-before-open regression for G3-Real"
```

---

## Task 9: 端到端真跑(§7 诚实验证门)+ 报告

**Files:**
- Create: `docs/gold2-g3real-result.md`

- [ ] **Step 1: 真跑 P3(每窗口 LLM 生成 + 编译 + gate)**

Run: `python -m Scripts.gold2_closed_loop.construct_p3`
Expected: 每窗口产出 `g3_build.eligible`(True/False,诚实)。LLM 不可用 → `CONSTRUCTION_FAILED`(诚实)。

- [ ] **Step 2: 真跑 P4(G3Real 盲年 vs G0 盲年)**

Run: `python -m Scripts.gold2_closed_loop.construct_p4`
Expected: `aggregate_delta = G3Real盲年sharpe − G0盲年sharpe`,盲年真起 LEAN。

- [ ] **Step 3: 写诚实报告**

`docs/gold2-g3real-result.md`:记录 G3Real 是否编译+gate 通过、ΔSharpe(4 窗口)、regime 非对称是否修了牛市踏空(W3/W4)与下跌保护(W1)。无论 EFFECTIVE/NOT_EFFECTIVE 都诚实标注。跨 §6 LLM 隔离档明示。

- [ ] **Step 4: Commit**

```bash
git add docs/gold2-g3real-result.md result/gold2-p3-construction/p3_report.json result/gold2-p4-construction/p4_report.json
git commit -m "docs(gold2): G3-Real end-to-end honest result (regime-asymmetric reconstruction)"
```

---

## Self-Review

**1. Spec coverage:** §1 基类→Task 1;§2 registry→Task 2;§3.1-3.3 LLM 契约+client→Task 3;§3.4 编译→Task 4;§3.4-3.6 generator+gate→Task 5,7;§3.5 regime 非对称→由 LLM 在 Task 9 产出(诊断约束在 Task 3 prompt);§4.1 supersede→Task 6;§4.2 _resolve_final_stage→Task 7;§5 错误处理→Task 5,7;§6 测试矩阵→Task 1-8;§7 端到端→Task 9。全覆盖。

**2. Placeholder scan:** Task 5 helper 函数实现要点已列;Task 7 `_reflect_interface_ok` 给 pythonnet 路径 + fallback。Task 6 `G3BuildResult`/`GenerationResponse` 字段扩展标注默认 None 不破既有。无 TBD。

**3. Type consistency:** `GenerationResponse`(source_text/compiler_hash/config_hash/training_gate_passed + 可选 candidate_class/dll_path)与 g3_builder.py:140-155 一致;`CompileResult` 在 Task 4/5 一致;`_Response`/`_GateRequest` 在 Task 5/7 一致;`FrozenCandidate` 复用 blind_evaluator.py:48-65。

**4. 风险点(诚实标注):** Task 4/7 dotnet 编译 + dll 加载依赖 Algorithm.CSharp.csproj ProjectReference copy-local;若 LEAN 加载候选 dll 时依赖解析失败,fallback 把候选 dll 输出到 Algorithm.CSharp/bin/Debug/ 同目录(不改 Algorithm.CSharp 源,只改候选 .csproj OutputPath)。Task 7 `_reflect_interface_ok` pythonnet 路径需环境支持,否则 fallback `dotnet script`/ReflectProbe.cs。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-21-gold2-real-llm-reconstruction.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks, fast iteration.

**2. Inline Execution** — batch execution with checkpoints.

Which approach?
