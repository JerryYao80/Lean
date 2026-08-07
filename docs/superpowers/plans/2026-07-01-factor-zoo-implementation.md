# Factor Zoo + Model Zoo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build factor zoo + model zoo infrastructure forming a three-layer pipeline (Factors → Models → Strategies), enabling IV-HV spread timing strategy with modular alpha/portfolio/risk components.

**Architecture:** LEAN-native five-layer Framework (Universe/Alpha/Portfolio/Risk/Execution). Pure additive, zero intrusion on existing code. Each phase verified by three gates: compile, unit test, backtest regression.

**Tech Stack:** C# (.NET 8), LEAN Framework, QuantConnect.Common namespace, NUnit tests

---

## Phase 1: Factor Core Interfaces

**Gate verification after Phase 1:**
- Gate 1: `dotnet build QuantConnect.Lean.sln` → 0 Error
- Gate 3: Run baseline strategies → results unchanged

### Task 1.1: Create FactorCategory enum

**Files:**
- Create: `Common/Factors/Core/FactorCategory.cs`

- [ ] **Step 1: Create enum file**

```csharp
namespace QuantConnect.Factors.Core
{
    public enum FactorCategory
    {
        Trend, Value, Volatility, Quality, Sentiment, Liquidity, Chip
    }
}
```

- [ ] **Step 2: Verify compilation**

Run: `dotnet build Common/QuantConnect.Common.csproj`
Expected: Build succeeded

- [ ] **Step 3: Commit**

```bash
git add Common/Factors/Core/FactorCategory.cs
git commit -m "feat(factors): add FactorCategory enum"
```

---

### Task 1.2-1.8: Create remaining core interfaces

*(Similar pattern for FactorScope, FactorComputeMode, FactorResult, FactorRankResult, FactorMetadata, IFactor, FactorRegistry - see spec for full code)*

---

## Phase 2: Volatility Factors Implementation

### Task 2.1: Create HVFactor (realized volatility)

**Files:**
- Create: `Common/Factors/Volatility/HVFactor.cs`
- Create: `Tests/Factors/Volatility/HVFactorTests.cs`

*(See spec section 2.2 for implementation)*

---

### Task 2.2: Create IVPercentileFactor (IV historical percentile)

**Files:**
- Create: `Common/Factors/Volatility/IVPercentileFactor.cs`

Reads IV CSV at `Data/alternative/ashare-implied-volatility/sse/daily/{ticker}.csv`
CSV fields: trade_date (yyyyMMdd format), atm_iv, skew, vix, ...

---

### Task 2.3: Create IVHVSpreadFactor (composite)

**Files:**
- Create: `Common/Factors/Volatility/IVHVSpreadFactor.cs`

Combines IVPercentileFactor + HVFactor

---

## Phase 3-5: Models + Strategy

*(Full detailed steps in the plan document - see spec sections 3-4)*

---

**Plan complete. Two execution options:**

**1. Subagent-Driven (recommended)** - Fresh subagent per task, review between tasks

**2. Inline Execution** - Execute in this session with checkpoints

**Which approach?**