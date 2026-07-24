# 交易复盘(Trading Review)层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个 manifest 驱动的插件式交易复盘(复盘)层:通用跨策略框架,gold2 为首个 adapter,产 review.json + HTML tearsheet + InfluxDB review_* + Grafana dashboard + manifest `review:` 段,零 LEAN native 改动。

**Architecture:** Python 后置层在 `Scripts/review/`,背测后只读 LEAN 原生产物。`StrategyReviewAdapter` ABC 定义 `layer_attribution()`/`trade_narrative()` 协议,策略在 manifest `review:` 段声明 layer_names + adapter_module,`run_review.py` 经 importlib 解析 adapter。gold2 用 4 层望远镜分解(trend/vol_target/extreme_risk/realrate_cap),求和目标 = `Trade.ProfitLoss`(gross,fees 单列),long-only guard,无 state_trace 时降级残差分解。

**Tech Stack:** Python 3(.11)、stdlib `importlib`/`argparse`/`json`/`decimal`/`pathlib`、`jsonschema` 4.26、`ruamel.yaml` 0.18.16、`Jinja2` 3.1.6、C# .NET 10(仅 gold2 策略侧 SerializeRlState 扩展)。InfluxDB line protocol 复用 `Scripts/export_backtest_results_to_influx.py` 的 `InfluxPoint`/`write_lines_to_influx`。

**关键事实(实现者必读,源自真实代码核查):**
- `manifest_loader.py:62,94` — `StrategyManifest` 有 `raw: dict` 字段,`load_manifest()` line 94 `raw=raw` 逐字传全部 YAML。未知顶层 key(如 `review:`)自动存活到 `manifest.raw`。**不加 dataclass 字段**。
- `manifest_lint.py:6-9` — `LintResult(ok: bool, errors: list)`,需加 `warnings: list`。
- `evolution_scheduler.py:80` `fire_optimization(manifest_path, config, state_path)`,state 文件写入在 line 129,line 131 是方法尾部。line 146 `_check_and_fire` 是 `main()` 嵌套闭包,line 153 调 `fire_optimization`。
- `run_e2e.py:8` `e2e(manifest_path, config)` line 20 调 baseline,line 21 return。review step 插在 line 20 后 line 21 前。
- `export_backtest_results_to_influx.py:62` `InfluxPoint(measurement, tags, fields, timestamp_ns)` dataclass;line 321 `write_lines_to_influx(lines, influx_url, org, bucket, token, timeout_seconds=30)`;line 304 `escape_string_field(value)` 返回 `"..."`;line 300 `escape_key(value)`。env: `INFLUXDB_URL/ORG/BUCKET/TOKEN`(默认值见 `export_barra_cne5_unified.py:28-31`)。
- gold2 closedTrades(599 笔)每笔 16 键:`id, symbols, entryTime, entryPrice, direction(0=Long/1=Short,见 LEAN TradeDirection), quantity(无符号), exitTime, exitPrice, profitLoss(gross,含方向符号), totalFees, mae, mfe, duration(字符串"d.hh:mm:ss"), endTradeDrawdown, isWin, orderIds`。
- `tradeStatistics.totalProfitLoss` 是 **STRING**(经 JsonRoundingConverter),需 `decimal.Parse`/`Decimal()`。`totalPerformance` 顶层有 `tradeStatistics/portfolioStatistics/closedTrades`。
- `{algo}.json` `orders` 是 **dict**(key=orderId 字符串),每个 order 有 `orderSubmissionData.{bidPrice,askPrice,lastPrice}`(验证 997/997 存在)。
- `charts['Drawdown']['series']['Equity Drawdown']['values']` 是 `[[unix_epoch_seconds, value], ...]`(2368 点);`charts['Strategy Equity']['series']['Equity']` 同结构。
- `alpha-results.json` 是 **list**,insight 键:`id, groupId, sourceModel, generatedTime, createdTime, closeTime, symbol, ticker, type, reference, ..., direction, period, magnitude(STRING), confidence, weight, scoreIsFinal, scoreMagnitude, scoreDirection, estimatedValue, tag`。
- `order-events.json` 是 **list**(1220 项),键:`id, algorithmId, orderId, orderEventId, symbol, symbolValue, symbolPermtick, time, status, fillPrice, fillPriceCurrency, fillQuantity, direction, isAssignment, quantity`。
- `Algorithm.CSharp/Common/IRlStateExportable.cs` — 接口在策略项目自己的 Common(**非 LEAN core**)。
- `OptionVolArb5LayerStrategy.cs:270` `WriteRlStateTraceIfNeeded(DateTime barTime)` 是 gold2 要镜像的模式:读 `RL_TRACE_PATH` env,`File.AppendAllText` 写 `SerializeRlState(this)+"\n"`。
- gold2 `SerializeRlState`(`Gold2BetaVolTargetStrategy.cs:136`)当前已写 9 字段:`ts, strategy, tpv, cash_pct, positions, w_smooth, trend_dir, extreme_triggered, realrate_cap`。**但 OnData 从未调用它**(无 WriteRlStateTraceIfNeeded)。需补 4 字段:`dir_coef, w_after_vol, extreme_cap, trend_disabled`,并接 OnData 调用点。
- `Gold2TrendAlphaModel.cs:54` 算 `dir`(int)和 `confirm`(RawValue),但**不存 LastDirCoef**。`Update()` 里 `_disabled` 分支 dirCoef=1.0。
- `Gold2VolTargetPortfolioModel.cs:18` `_lastActualWeight`(private)+ line 39 `_lastActualWeight = target;`(post-deadzone)。需提升 public getter。
- gold2 manifest 当前 10 个 parameter_space,**缺 `trend-disable`**(策略 `GetTunableParameterNames` 已含,line 133)。state_schema `dim_hint: 9`。
- Grafana datasource:`{"type":"influxdb","uid":"lean-influxdb"}`。schemaVersion 38-39。template var `algorithm_id` 用 `SHOW TAG VALUES FROM "lean_chart" WITH KEY = "algorithm_id"`。
- 测试模式:`Scripts/test_barra_dashboard_regression.py` 用 `_panel_fingerprint(p)=(title,(h,w,x,y),type)`,baseline 存 `Scripts/_barra_baseline_43.json` 是 `[[title,[h,w,x,y],type],...]`。
- 依赖已装:ruamel.yaml 0.18.16、jsonschema 4.26.0、jinja2 3.1.6。

---

## File Structure

### 新建 — Python review 层

| 文件 | 职责 |
|---|---|
| `Scripts/review/__init__.py` | package marker |
| `Scripts/review/adapters/__init__.py` | adapters subpackage marker |
| `Scripts/review/adapters/base.py` | `StrategyReviewAdapter` ABC + `TradeRecord` + `TradeContext` + `ReviewResult` dataclass |
| `Scripts/review/adapters/gold2.py` | `Gold2ReviewAdapter` — 4 层望远镜 + 残差降级 |
| `Scripts/review/artifacts.py` | LEAN artifact 加载 + TradeContext 构造(no-lookahead) |
| `Scripts/review/schema/review_schema.json` | review.json JSON Schema |
| `Scripts/review/tearsheet/builder.py` | HTML 生成器(Jinja2 + inline SVG) |
| `Scripts/review/tearsheet/templates/__init__.py` | 模板目录 marker |
| `Scripts/review/tearsheet/templates/gold2.html.j2` | Jinja2 模板 |
| `Scripts/review/influx_export.py` | review_* InfluxDB 导出(复用 InfluxPoint/write_lines_to_influx) |
| `Scripts/review/run_review.py` | CLI 入口 + bootstrap |
| `Scripts/review/cli.py` | argparse + 主流程 |
| `Scripts/review/pipeline_overlay.py` | 可选管线阶段逻辑(should_run + run_if_scheduled) |
| `Tests/test_review_base_adapter.py` | ABC 合规 + TradeRecord |
| `Tests/test_review_gold2_attribution.py` | 4 层望远镜 + 求和不变式 + long-only guard + 降级 |
| `Tests/test_review_schema.py` | review_schema.json 验证 |
| `Tests/test_review_no_lookahead.py` | state_trace 时序读取 |
| `Tests/test_review_cli.py` | run_review 端到端(用真实 gold2 artifacts) |
| `Tests/test_review_tearsheet.py` | HTML 生成 + self-check |
| `Tests/test_review_influx_export.py` | InfluxDB line-protocol |
| `Tests/test_review_manifest_gold2.py` | manifest review 段 |
| `Tests/test_review_pipeline_overlay.py` | overlay + manifest_lint warnings |
| `Tests/test_review_e2e_integration.py` | 全栈集成 |
| `Tests/Algorithm/Gold2/Gold2ReviewStateExportTests.cs` | C# state 字段 |
| `Scripts/test_strategy_review_dashboard_regression.py` | Grafana dashboard regression |
| `Scripts/_strategy_review_baseline_6.json` | 6-panel baseline |

### 新建 — Grafana

| 文件 | 职责 |
|---|---|
| `monitoring/grafana/dashboards/lean/strategy-review.json` | 6-panel dashboard |

### 修改 — gold2 策略侧 C#(非 LEAN core)

| 文件 | 改动 |
|---|---|
| `Algorithm.CSharp/Models/Gold2/Gold2TrendAlphaModel.cs` | 加 `LastDirCoef` public 字段,Update 里赋值 |
| `Algorithm.CSharp/Models/Gold2/Gold2VolTargetPortfolioModel.cs` | 加 `LastActualWeight` public getter |
| `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs` | SerializeRlState +4 字段 + WriteRlStateTraceIfNeeded + OnData 调用点 + `_trendAlpha`/`_portfolio`/`_rlTracePath`/`_trendDisabled`/`_extremeCap` 字段 |
| `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml` | 加 `review:` 段 + parameter_space 补 `trend-disable` + state_schema 4 字段 + dim_hint 13 |

### 修改 — 管线侧 Python

| 文件 | 改动 |
|---|---|
| `Scripts/auto_optimize/run_e2e.py` | e2e() baseline 后插 review step(gated) |
| `Scripts/auto_optimize/evolution_scheduler.py` | fire_optimization 末尾挂 pipeline_overlay |
| `Scripts/auto_optimize/manifest_lint.py` | LintResult 加 `warnings: list` |

### 不改

LEAN core(`Common/`、`Engine/`、`Report/`)、现有 14 个 Grafana dashboard、现有 manifest(gold2 除外)。

---

## Task 1: StrategyReviewAdapter ABC + TradeRecord + ReviewResult

**Files:**
- Create: `Scripts/review/__init__.py`
- Create: `Scripts/review/adapters/__init__.py`
- Create: `Scripts/review/adapters/base.py`
- Test: `Tests/test_review_base_adapter.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_review_base_adapter.py`:
```python
"""Tests for StrategyReviewAdapter ABC + TradeRecord + ReviewResult. Spec §3.1, §6.1."""
import sys
from decimal import Decimal
from pathlib import Path

# bootstrap Scripts/review onto sys.path so `from adapters.base import ...` resolves
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))

from adapters.base import (  # noqa: E402
    StrategyReviewAdapter, TradeRecord, TradeContext, ReviewResult,
)


def test_trade_record_carries_direction_sign_in_profit_loss():
    """quantity unsigned; direction sign lives in profit_loss (LEAN TradeBuilder sign)."""
    t = TradeRecord(
        symbol="518880", entry_time="2020-07-01", entry_price=Decimal("10.0"),
        exit_time="2020-07-05", exit_price=Decimal("11.0"),
        quantity=Decimal("100"), side="long",
        profit_loss=Decimal("100.0"), fees=Decimal("5.0"),
        tpv_entry=Decimal("1000.0"),
    )
    assert t.realized_pnl == Decimal("95.0")  # profit_loss - fees
    assert t.quantity == Decimal("100")  # unsigned


def test_abc_cannot_instantiate_directly():
    import pytest
    with pytest.raises(TypeError):
        StrategyReviewAdapter()  # abstract


class _GoodAdapter(StrategyReviewAdapter):
    LAYERS = ["a", "b"]
    def layer_attribution(self, trade, context):
        return {"a": Decimal("1"), "b": Decimal("4")}
    def trade_narrative(self, trade, context):
        return {"note": "ok"}
    @property
    def sum_to_property(self):
        return "profit_loss"


class _BadAdapter(StrategyReviewAdapter):
    LAYERS = ["a", "b"]
    def layer_attribution(self, trade, context):
        return {"a": Decimal("6")}  # sum != profit_loss; missing "b"
    def trade_narrative(self, trade, context):
        return {}
    @property
    def sum_to_property(self):
        return "profit_loss"


def test_good_adapter_sums_to_profit_loss():
    ad = _GoodAdapter()
    t = TradeRecord("518880", "t0", Decimal("1"), "t1", Decimal("1"),
                    Decimal("1"), "long", Decimal("5"), Decimal("0"), Decimal("1"))
    result = ad.layer_attribution(t, None)
    assert set(result.keys()) == {"a", "b"}
    assert sum(result.values()) == Decimal("5")  # == profit_loss
    assert ad.sum_to_property == "profit_loss"


def test_review_result_holds_blocks():
    rr = ReviewResult(
        run_meta={"strategy_name": "x", "schema_version": "1"},
        layer_attribution={},
        per_trade_narrative=[],
        drawdown_attribution=[],
        tca=None,
    )
    assert rr.run_meta["strategy_name"] == "x"
    assert rr.tca is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_base_adapter.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'adapters'` (file not created yet).

- [ ] **Step 3: Write minimal implementation**

`Scripts/review/__init__.py`:
```python
"""Trading-review (复盘) layer. Spec docs/superpowers/specs/2026-07-10-trading-review-design.md."""
```

`Scripts/review/adapters/__init__.py`:
```python
"""Strategy review adapters."""
```

`Scripts/review/adapters/base.py`:
```python
"""StrategyReviewAdapter ABC + TradeRecord/TradeContext/ReviewResult. Spec §3.1.

Generic cross-strategy protocol. Each strategy implements layer_attribution()
returning one Decimal per declared LAYERS; the values MUST sum to
trade.profit_loss (LEAN Trade.ProfitLoss, gross, pre-fees). fees ride on
TradeRecord and are shown as a reconciling line, never folded into a layer.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class TradeRecord:
    """One closed LEAN Trade. quantity is unsigned; direction sign lives in profit_loss."""
    symbol: str
    entry_time: str          # ISO-8601; matches closedTrade.entryTime
    entry_price: Decimal
    exit_time: str
    exit_price: Decimal
    quantity: Decimal        # Trade.Quantity, unsigned
    side: str                # "long" | "short" (mapped from TradeDirection 0/1)
    profit_loss: Decimal     # Trade.ProfitLoss, gross, carries direction sign
    fees: Decimal            # Trade.TotalFees, always positive
    tpv_entry: Decimal       # TotalPortfolioValue frozen at entry
    order_ids: list = field(default_factory=list)  # Trade.OrderIds

    @property
    def realized_pnl(self) -> Decimal:
        """Net of fees. Derived, NOT the sum-to target (spec §3.1)."""
        return self.profit_loss - self.fees


@dataclass
class TradeContext:
    """Per-trade state the adapter needs for attribution. Populated from state_trace."""
    entry_bar: dict = None   # {dir_coef, w_after_vol, extreme_cap, trend_disabled, ...} at ts<=entry
    realized_bar: dict = None  # first row ts>entry (post-fill weight)
    entry_signal: dict = None  # matched alpha insight
    regime_at_entry: dict = None


@dataclass
class ReviewResult:
    """Output of an adapter run; serialized to review.json."""
    run_meta: dict
    layer_attribution: dict          # {layer_name: {pnl_abs, pnl_pct_of_total, ...}}
    per_trade_narrative: list
    drawdown_attribution: list
    tca: Any                         # dict or None


class StrategyReviewAdapter(ABC):
    """Generic protocol. Implementations live in adapters/<strategy>.py."""

    LAYERS: list[str]  # ordered, e.g. ["trend","vol_target","extreme_risk","realrate_cap"]

    @abstractmethod
    def layer_attribution(self, trade: TradeRecord, context: TradeContext) -> dict[str, Decimal]:
        """Return one Decimal per name in LAYERS. MUST sum to trade.profit_loss (LEAN gross)."""

    @abstractmethod
    def trade_narrative(self, trade: TradeRecord, context: TradeContext) -> dict:
        """Per-trade narrative: entry_signal, regime, insight realized-vs-predicted."""

    @property
    @abstractmethod
    def sum_to_property(self) -> str:
        """Which TradeRecord field the layer contributions sum to. Always 'profit_loss'."""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_base_adapter.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/__init__.py Scripts/review/adapters/__init__.py Scripts/review/adapters/base.py Tests/test_review_base_adapter.py
git commit -m "feat(review): StrategyReviewAdapter ABC + TradeRecord/TradeContext/ReviewResult"
```

---

## Task 2: review.json JSON Schema

**Files:**
- Create: `Scripts/review/schema/review_schema.json`
- Test: `Tests/test_review_schema.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_review_schema.py`:
```python
"""Tests for review_schema.json. Spec §2, §6.1."""
import json
import sys
from pathlib import Path

import jsonschema
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
SCHEMA = json.loads((_REPO / "Scripts" / "review" / "schema" / "review_schema.json").read_text())


def _good_doc():
    return {
        "run_meta": {
            "strategy_name": "Gold2BetaVolTargetStrategy",
            "backtest_id": "Gold2BetaVolTargetStrategy",
            "period_start": "2020-07-01",
            "period_end": "2026-06-23",
            "total_closed_trade_pnl": "12345.67",
            "generated_at": "2026-07-10T00:00:00Z",
            "adapter_version": "1.0.0",
            "schema_version": "1",
        },
        "layer_attribution": {
            "trend": {"pnl_abs": "100.0", "pnl_pct_of_total": 0.5, "n_trades": 10,
                      "n_wins": 6, "contribution_to_total_return": 0.05},
            "vol_target": {"pnl_abs": "50.0", "pnl_pct_of_total": 0.25, "n_trades": 10,
                           "n_wins": 5, "contribution_to_total_return": 0.025},
            "extreme_risk": {"pnl_abs": "-20.0", "pnl_pct_of_total": -0.1, "n_trades": 3,
                             "n_wins": 1, "contribution_to_total_return": -0.01},
            "realrate_cap": {"pnl_abs": "70.0", "pnl_pct_of_total": 0.35, "n_trades": 8,
                             "n_wins": 4, "contribution_to_total_return": 0.035},
        },
        "per_trade_narrative": [
            {"trade_id": 1, "symbol": "518880", "entry_time": "2020-07-01",
             "exit_time": "2020-07-05", "entry_price": "10.0", "exit_price": "11.0",
             "direction": "long", "quantity": "100", "pnl": "100.0", "fees": "5.0",
             "mae": "0.0", "mfe": "1.5", "end_trade_drawdown": "0.0", "days_held": 4,
             "layer_contributions": {"trend": "100.0"}},
        ],
        "drawdown_attribution": [
            {"peak_time": "2021-01-01", "trough_time": "2021-03-01",
             "recovery_time": "2021-06-01", "depth_pct": -0.15,
             "top_contributing_layers": [{"layer": "trend", "pnl_abs": "-50.0"}],
             "top_contributing_trades": [5, 12], "regime_during": "RISING_FAST",
             "regime_source": "state_trace"},
        ],
        "tca": {"avg_slippage_bps": 1.2, "fill_quality_score": 0.95, "n_fills": 1220,
                "source": "orderSubmissionData"},
    }


def test_good_doc_validates():
    jsonschema.validate(_good_doc(), SCHEMA)  # no exception


@pytest.mark.parametrize("missing", ["run_meta", "layer_attribution", "per_trade_narrative", "drawdown_attribution", "tca"])
def test_missing_top_level_fails(missing):
    doc = _good_doc()
    doc.pop(missing)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


def test_layer_attribution_layer_missing_required_field_fails():
    doc = _good_doc()
    doc["layer_attribution"]["trend"].pop("pnl_abs")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


def test_tca_can_be_null():
    """tca block present but null is allowed (artifact-trimmed case)."""
    doc = _good_doc()
    doc["tca"] = None
    jsonschema.validate(doc, SCHEMA)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_schema.py -v
```
Expected: FAIL — `FileNotFoundError` (schema not created).

- [ ] **Step 3: Write minimal implementation**

`Scripts/review/schema/review_schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "review.json",
  "type": "object",
  "required": ["run_meta", "layer_attribution", "per_trade_narrative", "drawdown_attribution", "tca"],
  "properties": {
    "run_meta": {
      "type": "object",
      "required": ["strategy_name", "backtest_id", "period_start", "period_end", "total_closed_trade_pnl", "generated_at", "adapter_version", "schema_version"],
      "properties": {
        "strategy_name": {"type": "string"},
        "backtest_id": {"type": "string"},
        "period_start": {"type": "string"},
        "period_end": {"type": "string"},
        "total_closed_trade_pnl": {"type": "string"},
        "generated_at": {"type": "string"},
        "adapter_version": {"type": "string"},
        "schema_version": {"type": "string"}
      }
    },
    "layer_attribution": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "required": ["pnl_abs", "pnl_pct_of_total", "n_trades", "n_wins", "contribution_to_total_return"],
        "properties": {
          "pnl_abs": {"type": ["string", "number"]},
          "pnl_pct_of_total": {"type": "number"},
          "n_trades": {"type": "integer"},
          "n_wins": {"type": "integer"},
          "contribution_to_total_return": {"type": "number"}
        }
      }
    },
    "per_trade_narrative": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["trade_id", "symbol", "entry_time", "exit_time", "entry_price", "exit_price", "direction", "quantity", "pnl", "fees", "mae", "mfe", "end_trade_drawdown", "days_held", "layer_contributions"],
        "properties": {
          "trade_id": {"type": ["integer", "string"]},
          "symbol": {"type": "string"},
          "entry_time": {"type": "string"},
          "exit_time": {"type": "string"},
          "entry_price": {"type": ["string", "number"]},
          "exit_price": {"type": ["string", "number"]},
          "direction": {"type": "string", "enum": ["long", "short"]},
          "quantity": {"type": ["string", "number"]},
          "pnl": {"type": ["string", "number"]},
          "fees": {"type": ["string", "number"]},
          "mae": {"type": ["string", "number"]},
          "mfe": {"type": ["string", "number"]},
          "end_trade_drawdown": {"type": ["string", "number"]},
          "days_held": {"type": "integer"},
          "layer_contributions": {"type": "object"},
          "entry_signal": {"type": "object"},
          "regime_at_entry": {"type": "object"},
          "insight_realized_vs_predicted": {"type": "object"}
        }
      }
    },
    "drawdown_attribution": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["peak_time", "trough_time", "depth_pct", "top_contributing_layers", "top_contributing_trades"],
        "properties": {
          "peak_time": {"type": "string"},
          "trough_time": {"type": "string"},
          "recovery_time": {"type": ["string", "null"]},
          "depth_pct": {"type": "number"},
          "top_contributing_layers": {"type": "array"},
          "top_contributing_trades": {"type": "array"},
          "regime_during": {"type": ["string", "null"]},
          "regime_source": {"type": "string"}
        }
      }
    },
    "tca": {
      "type": ["object", "null"],
      "properties": {
        "avg_slippage_bps": {"type": ["number", "null"]},
        "fill_quality_score": {"type": ["number", "null"]},
        "n_fills": {"type": "integer"},
        "source": {"type": "string"}
      }
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_schema.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/schema/review_schema.json Tests/test_review_schema.py
git commit -m "feat(review): review.json JSON Schema + validation tests"
```

---

## Task 3: gold2 策略侧 C# — LastDirCoef + LastActualWeight + SerializeRlState 扩展

**Files:**
- Modify: `Algorithm.CSharp/Models/Gold2/Gold2TrendAlphaModel.cs`
- Modify: `Algorithm.CSharp/Models/Gold2/Gold2VolTargetPortfolioModel.cs`
- Modify: `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs`
- Test: `Tests/Algorithm/Gold2/Gold2ReviewStateExportTests.cs`

> **非 LEAN core**:`Gold2TrendAlphaModel`/`Gold2VolTargetPortfolioModel` 在 `Algorithm.CSharp/Models/Gold2/`(策略项目);`IRlStateExportable` 在 `Algorithm.CSharp/Common/`(策略项目自己的 common)。不改 `Common/`、`Engine/`。

- [ ] **Step 1: Write the failing test**

`Tests/Algorithm/Gold2/Gold2ReviewStateExportTests.cs`:
```csharp
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Algorithm.CSharp.Models.Gold2;

namespace QuantConnect.Tests.Algorithm.Gold2
{
    [TestFixture]
    public class Gold2ReviewStateExportTests
    {
        [Test]
        public void TrendAlphaModel_Exposes_LastDirCoef()
        {
            // LastDirCoef is the insight.Weight the model last emitted; review adapter reads it
            // to attribute the trend layer. Must be public + default 0 before first Update.
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, Symbol.Empty, 0.2m, false);
            Assert.AreEqual(0m, model.LastDirCoef);
        }

        [Test]
        public void VolTargetPortfolioModel_Exposes_LastActualWeight()
        {
            // LastActualWeight is the post-deadzone target; review adapter reads it for the
            // vol_target layer. Must be public + default 0 before first CreateTargets.
            var vol = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            var model = new Gold2VolTargetPortfolioModel(vol, Symbol.Empty, 0.05m);
            Assert.AreEqual(0m, model.LastActualWeight);
        }

        [Test]
        public void SerializeRlState_Includes4ReviewFields()
        {
            // Spec §3.3: dir_coef, w_after_vol, extreme_cap, trend_disabled must be emitted.
            var s = new Gold2BetaVolTargetStrategy();
            // SerializeRlState signature is (QCAlgorithm algo); use a minimal stub.
            var algo = new TestAlgorithm();
            algo.SetStartDate(2020, 1, 1);
            algo.SetCash(1_000_000);
            var json = s.SerializeRlState(algo);
            Assert.IsTrue(json.Contains("\"dir_coef\""), "missing dir_coef");
            Assert.IsTrue(json.Contains("\"w_after_vol\""), "missing w_after_vol");
            Assert.IsTrue(json.Contains("\"extreme_cap\""), "missing extreme_cap");
            Assert.IsTrue(json.Contains("\"trend_disabled\""), "missing trend_disabled");
        }

        // Minimal QCAlgorithm stub for SerializeRlState (avoids full AlgorithmStub fixture).
        private class TestAlgorithm : QCAlgorithm
        {
            public new DateTime Time => new(2020, 7, 1);
        }
    }
}
```

> 若 `TestAlgorithm` 编译受阻(QCAlgorithm 构造依赖),fallback:用反射检查 `SerializeRlState` 方法存在 + 在真实 backtest artifacts 的 `state_trace.jsonl`(Task 3 接好后跑一次 backtest with `RL_TRACE_PATH` set)上验证字段。优先尝试 stub。

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2ReviewStateExportTests" --logger "console;verbosity=normal" 2>&1 | tail -20
```
Expected: FAIL — `LastDirCoef`/`LastActualWeight` not found (CS1061), `dir_coef` missing from JSON.

- [ ] **Step 3: Write minimal implementation**

Modify `Algorithm.CSharp/Models/Gold2/Gold2TrendAlphaModel.cs` — add public field + assign in `Update()`. Insert after line 22 (`private readonly bool _disabled;`):
```csharp
        /// <summary>Last dirCoef emitted (insight.Weight). Review adapter reads this for the
        /// trend-layer attribution (spec §3.3). 0 before first Update.</summary>
        public decimal LastDirCoef;
```
Then in `Update()`, the `_disabled` branch (line 40-49): after `yield return new Insight(... 1.0 ...)` add before `yield break;`:
```csharp
                LastDirCoef = 1.0m;
```
And in the non-disabled branch (line 71-80): before `yield return new Insight(... (double)weight ...)` add:
```csharp
            LastDirCoef = weight;
```

Modify `Algorithm.CSharp/Models/Gold2/Gold2VolTargetPortfolioModel.cs` — add public getter. Insert after line 18 (`private decimal _lastActualWeight;`):
```csharp
        /// <summary>Last post-deadzone target weight. Review adapter reads this for the
        /// vol_target-layer attribution (spec §3.3). 0 before first CreateTargets.</summary>
        public decimal LastActualWeight => _lastActualWeight;
```

Modify `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs`:

(a) Add fields after line 39 (`private readonly Queue<decimal> _rvolReturns = new();`):
```csharp
        // RL state trace: RL_TRACE_PATH env triggers per-bar state write (mirrors
        // OptionVolArb5LayerStrategy.cs:270). Spec §3.3 — review adapter consumes this.
        private string _rlTracePath;
        private bool _trendDisabled;
        private decimal _extremeCap;
        private Gold2TrendAlphaModel _trendAlpha;
        private Gold2VolTargetPortfolioModel _portfolio;
```

(b) In `Initialize()`, store refs + cap. After line 75 (`bool trendDisabled = ...`), before line 76 `SetAlpha(...)`:
```csharp
            _trendDisabled = trendDisabled;
            _extremeCap = GetDecimalParameter("extreme-vol-cap", 0.3m);
            _trendAlpha = new Gold2TrendAlphaModel(_trend, _gold, GetDecimalParameter("trend-floor", 0.2m), trendDisabled);
            SetAlpha(_trendAlpha);
            _portfolio = new Gold2VolTargetPortfolioModel(_vol, _gold, GetDecimalParameter("rebalance-threshold", 0.05m));
            SetPortfolioConstruction(_portfolio);
```
Replace original lines 76-77 (the old `SetAlpha`/`SetPortfolioConstruction` calls).

(c) Add `WriteRlStateTraceIfNeeded` method (insert before `SerializeRlState`, line 136). Mirror `OptionVolArb5LayerStrategy.cs:270-287`:
```csharp
        /// <summary>RL_TRACE_PATH env triggers per-bar state write. Mirrors OptionVolArb5LayerStrategy.
        /// Called at end of OnData so LastDirCoef/LastActualWeight/extreme_triggered are current-bar.</summary>
        private void WriteRlStateTraceIfNeeded(DateTime barTime)
        {
            if (_rlTracePath == null)
                _rlTracePath = Environment.GetEnvironmentVariable("RL_TRACE_PATH") ?? "";
            if (string.IsNullOrEmpty(_rlTracePath)) return;
            try
            {
                File.AppendAllText(_rlTracePath, SerializeRlState(this) + "\n");
            }
            catch (Exception ex)
            {
                Log($"[Gold2] RL_TRACE_PATH write failed: {ex.Message}");
            }
        }
```
Add `using System.IO;` to the using block at top (after line 1 `using System;`).

(d) In `OnData`, call `WriteRlStateTraceIfNeeded` at the END (after the DFII10 block, before the closing `}` of OnData, line 116):
```csharp
            WriteRlStateTraceIfNeeded(data.Time);
```

(e) Extend `SerializeRlState` (line 142-150) — add 4 fields to the anonymous object. Replace the `return JsonConvert.SerializeObject(new {...})` block:
```csharp
            return JsonConvert.SerializeObject(new
            {
                ts = algo.Time.ToString("o"), strategy = "Gold2BetaVolTargetStrategy",
                tpv, cash_pct = Portfolio.Cash / tpv, positions,
                w_smooth = _vol.Compute(_gold, algo.Time).Value,
                trend_dir = (int)_trend.Compute(_gold, algo.Time).Value,
                extreme_triggered = (int)_ext.Compute(_gold, algo.Time).Value == 1,
                realrate_cap = _realrate.Compute(_gold, algo.Time).Value,
                // Review-adapter fields (spec §3.3): telescoping decomposition inputs.
                dir_coef = _trendAlpha.LastDirCoef,
                w_after_vol = _portfolio.LastActualWeight,
                extreme_cap = _extremeCap,
                trend_disabled = _trendDisabled
            });
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj 2>&1 | tail -5
cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2ReviewStateExportTests" --logger "console;verbosity=normal" 2>&1 | tail -20
```
Expected: build 0 errors; 3 tests PASS. If `TestAlgorithm` stub fails to construct, fall back to reflection check for the 4 field names + skip the JSON-content assertion with a note.

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/Models/Gold2/Gold2TrendAlphaModel.cs Algorithm.CSharp/Models/Gold2/Gold2VolTargetPortfolioModel.cs Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs Tests/Algorithm/Gold2/Gold2ReviewStateExportTests.cs
git commit -m "feat(gold2): SerializeRlState +4 review fields + LastDirCoef/LastActualWeight + WriteRlStateTraceIfNeeded"
```

---

## Task 4: gold2 manifest — review 段 + trend-disable + state_schema 4 字段

**Files:**
- Modify: `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`
- Test: `Tests/test_review_manifest_gold2.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_review_manifest_gold2.py`:
```python
"""Tests for gold2 manifest review: block + trend-disable + state_schema. Spec §5.1, §3.3."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from manifest_loader import load_manifest  # noqa: E402

MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"


def test_manifest_has_review_block():
    m = load_manifest(MANIFEST)
    r = m.raw.get("review", {})
    assert r, "review: block missing"
    assert r["adapter_module"] == "adapters.gold2"
    assert r["adapter_class"] == "Gold2ReviewAdapter"
    assert r["layer_names"] == ["trend", "vol_target", "extreme_risk", "realrate_cap"]
    assert r.get("block_deploy") is False
    assert "thresholds" in r


def test_trend_disable_in_parameter_space():
    m = load_manifest(MANIFEST)
    names = [p.name for p in m.parameter_space]
    assert "trend-disable" in names, "trend-disable missing from parameter_space (strategy GetTunableParameterNames has it)"


def test_state_schema_has_4_review_fields():
    m = load_manifest(MANIFEST)
    names = {f.name for f in m.state_schema.fields}
    for f in ["dir_coef", "w_after_vol", "extreme_cap", "trend_disabled"]:
        assert f in names, f"state field {f} missing (SerializeRlState emits it)"
    assert m.state_schema.dim_hint == 13
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_manifest_gold2.py -v
```
Expected: FAIL — `review` block missing, `trend-disable` not in parameter_space, dim_hint still 9.

- [ ] **Step 3: Write minimal implementation**

Modify `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`:

(a) Add `trend-disable` to `parameter_space` (after the `trend-floor` line, line 16):
```yaml
  - {name: trend-disable, type: bool, range: [false, true], default: false, layer: L2_Alpha}
```

(b) Extend `state_schema.fields` — add 4 fields before `dim_hint` (after `realrate_cap` line, line 27):
```yaml
    - {name: dir_coef, type: float}
    - {name: w_after_vol, type: float}
    - {name: extreme_cap, type: float}
    - {name: trend_disabled, type: bool}
```
Change `dim_hint: 9` → `dim_hint: 13`.

(c) Add `review:` block at the end of the file (after `universe:`, line 36):
```yaml
review:
  adapter_module: adapters.gold2
  adapter_class: Gold2ReviewAdapter
  layer_names: [trend, vol_target, extreme_risk, realrate_cap]
  review_schedule: on_backtest
  block_deploy: false
  last_review: null
  review_status: null
  review_artifact_path: null
  thresholds:
    max_layer_attribution_gap: 0.15
    min_narrative_trades: 20
    max_consecutive_dd_days: 15
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_manifest_gold2.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml Tests/test_review_manifest_gold2.py
git commit -m "feat(gold2): manifest review: block + trend-disable param + 4 state_schema fields"
```

---

## Task 5: Gold2ReviewAdapter — 4 层望远镜归因(带 state_trace)

**Files:**
- Create: `Scripts/review/adapters/gold2.py`
- Test: `Tests/test_review_gold2_attribution.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_review_gold2_attribution.py`:
```python
"""Tests for Gold2ReviewAdapter 4-layer telescoping attribution. Spec §3.2, §6.1."""
import sys
from decimal import Decimal
from pathlib import Path
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
from adapters.base import TradeRecord, TradeContext  # noqa: E402
from adapters.gold2 import Gold2ReviewAdapter  # noqa: E402


def _trade(profit_loss="100.0", entry="10.0", exit_p="11.0", side="long"):
    return TradeRecord(
        symbol="518880", entry_time="2020-07-01", entry_price=Decimal(entry),
        exit_time="2020-07-05", exit_price=Decimal(exit_p),
        quantity=Decimal("100"), side=side,
        profit_loss=Decimal(profit_loss), fees=Decimal("0"),
        tpv_entry=Decimal("1000.0"),
    )


def _ctx(dir_coef=1.0, w_after_vol=0.5, extreme_triggered=False, extreme_cap=0.3,
         realrate_cap=0.6, trend_disabled=False):
    return TradeContext(entry_bar={
        "dir_coef": dir_coef, "w_after_vol": w_after_vol,
        "extreme_triggered": extreme_triggered, "extreme_cap": extreme_cap,
        "realrate_cap": realrate_cap, "trend_disabled": trend_disabled,
    })


def test_layers_constant():
    assert Gold2ReviewAdapter.LAYERS == ["trend", "vol_target", "extreme_risk", "realrate_cap"]


def test_sum_to_profit_loss_no_caps():
    """extreme/realrate not triggered: w_real = w_vol, δQ_lot = 0, sum == profit_loss."""
    ad = Gold2ReviewAdapter()
    t = _trade()
    c = _ctx(dir_coef=1.0, w_after_vol=0.5, extreme_triggered=False, realrate_cap=0.6)
    layers = ad.layer_attribution(t, c)
    assert sum(layers.values()) == pytest.approx(Decimal("100.0"), abs=Decimal("1e-9"))


def test_sum_to_profit_loss_extreme_triggered():
    ad = Gold2ReviewAdapter()
    t = _trade()
    c = _ctx(dir_coef=1.0, w_after_vol=0.5, extreme_triggered=True, extreme_cap=0.3, realrate_cap=0.6)
    layers = ad.layer_attribution(t, c)
    assert sum(layers.values()) == pytest.approx(Decimal("100.0"), abs=Decimal("1e-9"))


def test_sum_to_profit_loss_both_caps():
    ad = Gold2ReviewAdapter()
    t = _trade()
    c = _ctx(dir_coef=1.0, w_after_vol=0.5, extreme_triggered=True, extreme_cap=0.3, realrate_cap=0.2)
    layers = ad.layer_attribution(t, c)
    assert sum(layers.values()) == pytest.approx(Decimal("100.0"), abs=Decimal("1e-9"))


def test_sum_to_profit_loss_trend_disabled():
    """trend-disabled: dir_coef=1.0, trend layer still carries the directional contribution."""
    ad = Gold2ReviewAdapter()
    t = _trade()
    c = _ctx(dir_coef=1.0, w_after_vol=0.5, trend_disabled=True)
    layers = ad.layer_attribution(t, c)
    assert sum(layers.values()) == pytest.approx(Decimal("100.0"), abs=Decimal("1e-9"))


def test_property_test_random_sums():
    """Random sane inputs: |sum - profit_loss| < 1e-9 (Decimal). Spec §6.1 property test."""
    import random
    ad = Gold2ReviewAdapter()
    rng = random.Random(42)
    for _ in range(200):
        dp = Decimal(str(rng.uniform(-5, 5)))   # Δp
        entry = Decimal("10.0")
        exit_p = entry + dp
        qty = Decimal(str(rng.randint(10, 1000)))
        profit_loss = dp * qty  # long: ProfitLoss = (exit-entry)*qty
        t = TradeRecord("518880", "t0", entry, "t1", exit_p, qty, "long", profit_loss, Decimal("0"), Decimal("10000"))
        c = _ctx(
            dir_coef=Decimal(str(rng.choice([0.2, 0.5, 1.0]))),
            w_after_vol=Decimal(str(rng.uniform(0.05, 0.8))),
            extreme_triggered=rng.random() < 0.3,
            extreme_cap=Decimal("0.3"),
            realrate_cap=Decimal(str(rng.choice([0.2, 0.4, 0.6]))),
        )
        layers = ad.layer_attribution(t, c)
        assert abs(sum(layers.values()) - profit_loss) < Decimal("1E-9"), \
            f"sum {sum(layers.values())} != {profit_loss} for ctx {c.entry_bar}"


def test_long_only_guard_raises_on_short():
    """Spec §3.2 long-only: short trade must raise, never silently apply long formula."""
    ad = Gold2ReviewAdapter()
    t = _trade(side="short")
    c = _ctx()
    with pytest.raises(ValueError, match="long-only"):
        ad.layer_attribution(t, c)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_gold2_attribution.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'adapters.gold2'`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/review/adapters/gold2.py`:
```python
"""Gold2ReviewAdapter — 4-layer telescoping PnL attribution. Spec §3.2.

Decomposition (long-only; Δp = exit_price - entry_price, signed; LEAN ProfitLoss
for Long already applies +1):
    scale  = TPV_e / p_e
    w_trend= dir_coef;  w_vol = w_after_vol
    w_ext  = extreme_triggered ? min(w_vol, extreme_cap) : w_vol
    w_real = min(w_ext, realrate_cap)
    Q_layer= w_layer * scale;   δQ_lot = Q - Q_real   (<=0)
    C_trend        = Q_trend * Δp
    C_vol_target   = (Q_vol - Q_trend) * Δp
    C_extreme_risk = (Q_ext - Q_vol)   * Δp
    C_realrate     = (Q_real - Q_ext)  * Δp + δQ_lot * Δp

Invariant: C_trend + C_vol_target + C_extreme_risk + C_realrate
         = Q_real*Δp + δQ_lot*Δp = (Q_real+δQ_lot)*Δp = Q*Δp = Trade.ProfitLoss (gross).
Every intermediate Q_layer cancels with its neighbor; δQ_lot forced into C_realrate.
"""
from decimal import Decimal
from .base import StrategyReviewAdapter, TradeRecord, TradeContext


class Gold2ReviewAdapter(StrategyReviewAdapter):
    LAYERS = ["trend", "vol_target", "extreme_risk", "realrate_cap"]

    @property
    def sum_to_property(self) -> str:
        return "profit_loss"

    def layer_attribution(self, trade: TradeRecord, context: TradeContext) -> dict[str, Decimal]:
        bar = (context.entry_bar if context else None) or None
        # Residual degradation (spec §3.5) handled in Task 6; here assume telescoping path.
        if not bar:
            zero = Decimal("0")
            return {"trend": trade.profit_loss, "vol_target": zero,
                    "extreme_risk": zero, "realrate_cap": zero}
        # long-only guard (spec §3.2): short trade must not silently apply long formula.
        if trade.side != "long":
            raise ValueError(
                f"gold2 is long-only (518880 不可做空); got side={trade.side}. "
                f"Short trade must fall back to residual (§3.5), not telescoping."
            )
        dir_coef = Decimal(str(bar.get("dir_coef", 1.0)))
        w_vol = Decimal(str(bar.get("w_after_vol", 0.0)))
        extreme_triggered = bool(bar.get("extreme_triggered", False))
        extreme_cap = Decimal(str(bar.get("extreme_cap", 0.3)))
        realrate_cap = Decimal(str(bar.get("realrate_cap", 0.6)))

        scale = trade.tpv_entry / trade.entry_price if trade.entry_price != 0 else Decimal("0")
        w_trend = dir_coef
        w_ext = min(w_vol, extreme_cap) if extreme_triggered else w_vol
        w_real = min(w_ext, realrate_cap)

        Q_trend = w_trend * scale
        Q_vol = w_vol * scale
        Q_ext = w_ext * scale
        Q_real = w_real * scale
        dp = trade.exit_price - trade.entry_price
        # Recover realized Q: prefer realized_bar.w_realized (post-fill), else PL/dp (long).
        if context and context.realized_bar and "w_realized" in (context.realized_bar or {}):
            Q = Decimal(str(context.realized_bar["w_realized"])) * scale
        elif dp != 0:
            Q = trade.profit_loss / dp   # exact: ProfitLoss = Q*dp for long
        else:
            Q = Q_real  # no price move; δQ_lot = 0
        delta_q_lot = Q - Q_real  # lot-floor + execution residual (<=0 typically)

        C_trend = Q_trend * dp
        C_vol_target = (Q_vol - Q_trend) * dp
        C_extreme_risk = (Q_ext - Q_vol) * dp
        C_realrate = (Q_real - Q_ext) * dp + delta_q_lot * dp
        return {
            "trend": C_trend,
            "vol_target": C_vol_target,
            "extreme_risk": C_extreme_risk,
            "realrate_cap": C_realrate,
        }

    def trade_narrative(self, trade: TradeRecord, context: TradeContext) -> dict:
        bar = (context.entry_bar if context else None) or None
        dp = trade.exit_price - trade.entry_price
        extreme_triggered = bool(bar.get("extreme_triggered", False)) if bar else False
        return {
            "entry_signal": (context.entry_signal if context else None) or {},
            "regime_at_entry": (context.regime_at_entry if context else None) or {},
            "extreme_cap_was_protective": dp < 0 and extreme_triggered,  # long-only narrative
            "attribution_method": "telescoping" if bar else "residual",
        }
```

> **注意**:Task 5 实现已含 residual 兜底(no bar → trend=PL,其余 0)和 attribution_method 标注,因此 Task 6(原本单独的降级 task)的实际改动只是补 short-trade-不-raise 的 residual 行为。Task 6 步骤会调整。

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_gold2_attribution.py -v
```
Expected: PASS (8 tests). The property test (200 random cases) is the key invariant check.

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/adapters/gold2.py Tests/test_review_gold2_attribution.py
git commit -m "feat(review): Gold2ReviewAdapter 4-layer telescoping attribution + long-only guard"
```

---

## Task 6: gold2 降级残差分解 + short-trade residual 行为

**Files:**
- Modify: `Scripts/review/adapters/gold2.py`
- Test: `Tests/test_review_gold2_attribution.py` (extend)

> Task 5 已实现 residual 兜底(no bar → trend=PL)。Task 6 补:residual 模式下 short trade **不**触发 long-only guard(guard 只在 telescoping 路径)。

- [ ] **Step 1: Write the failing test**

Append to `Tests/test_review_gold2_attribution.py`:
```python
def test_residual_degradation_when_no_state_trace():
    """Spec §3.5: state_trace absent → residual decomposition. trend=all PnL, others=0,
    attribution_method='residual'."""
    ad = Gold2ReviewAdapter()
    t = _trade()
    c = TradeContext(entry_bar=None, realized_bar=None)  # no state_trace
    layers = ad.layer_attribution(t, c)
    assert layers["trend"] == Decimal("100.0")
    assert layers["vol_target"] == Decimal("0")
    assert layers["extreme_risk"] == Decimal("0")
    assert layers["realrate_cap"] == Decimal("0")
    assert sum(layers.values()) == Decimal("100.0")
    narrative = ad.trade_narrative(t, c)
    assert narrative["attribution_method"] == "residual"


def test_residual_short_trade_does_not_raise():
    """In residual mode, short trade does NOT hit the long-only guard (guard only on telescoping)."""
    ad = Gold2ReviewAdapter()
    t = _trade(side="short", profit_loss="-50.0", entry="11.0", exit_p="10.5")
    c = TradeContext(entry_bar=None, realized_bar=None)
    layers = ad.layer_attribution(t, c)  # should not raise
    assert sum(layers.values()) == Decimal("-50.0")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_gold2_attribution.py::test_residual_short_trade_does_not_raise -v
```
Expected: FAIL — current Task 5 impl raises ValueError on short even in residual path (guard is before the `if not bar` check? — verify: in Task 5 the `if not bar: return {...}` is BEFORE the guard, so residual short does NOT raise. If test passes already, skip to Step 5. If it fails, move the `if not bar` block above the guard.)

- [ ] **Step 3: Write minimal implementation (if needed)**

In `Scripts/review/adapters/gold2.py` `layer_attribution`, ensure the residual `if not bar` block is the **first** check (before the long-only guard). Task 5 already has this order — verify and adjust if test failed:
```python
    def layer_attribution(self, trade, context):
        bar = (context.entry_bar if context else None) or None
        if not bar:                      # residual FIRST — no guard, short OK
            zero = Decimal("0")
            return {"trend": trade.profit_loss, "vol_target": zero,
                    "extreme_risk": zero, "realrate_cap": zero}
        if trade.side != "long":         # guard only on telescoping path
            raise ValueError(...)
        # ... telescoping ...
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_gold2_attribution.py -v
```
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/adapters/gold2.py Tests/test_review_gold2_attribution.py
git commit -m "feat(review): gold2 residual degradation + short-trade residual path (no guard)"
```

---

## Task 7: artifact loading + TradeContext 构造(no-lookahead)

**Files:**
- Create: `Scripts/review/artifacts.py`
- Test: `Tests/test_review_no_lookahead.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_review_no_lookahead.py`:
```python
"""Tests for artifact loading + TradeContext construction (no-lookahead). Spec §3.4, §6.1."""
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
from adapters.base import TradeRecord, TradeContext  # noqa: E402
from artifacts import load_closed_trades, load_state_trace, build_trade_context  # noqa: E402


def _write_state_trace(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_load_state_trace_returns_chronological():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "state_trace.jsonl"
        _write_state_trace(p, [
            {"ts": "2020-07-01T00:00:00", "dir_coef": 0.5, "w_after_vol": 0.4},
            {"ts": "2020-07-02T00:00:00", "dir_coef": 1.0, "w_after_vol": 0.5},
            {"ts": "2020-07-03T00:00:00", "dir_coef": 1.0, "w_after_vol": 0.6},
        ])
        trace = load_state_trace(p)
        assert len(trace) == 3
        assert trace[0]["dir_coef"] == 0.5


def test_build_context_reads_realized_from_first_row_after_entry():
    """Spec §3.4 (C3 P timing): realized weight from first ts>entry (post-fill),
    NOT the ts==entry row (pre-fill)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "state_trace.jsonl"
        _write_state_trace(p, [
            {"ts": "2020-07-01T00:00:00", "dir_coef": 0.5, "w_after_vol": 0.4, "w_smooth": 0.4},
            {"ts": "2020-07-02T00:00:00", "dir_coef": 1.0, "w_after_vol": 0.5, "w_smooth": 0.5},
            {"ts": "2020-07-03T00:00:00", "dir_coef": 1.0, "w_after_vol": 0.6, "w_smooth": 0.6},
        ])
        trace = load_state_trace(p)
        ctx = build_trade_context(
            entry_time="2020-07-01T00:00:00", exit_time="2020-07-05T00:00:00",
            symbol="518880", state_trace=trace, alpha_insights=[],
        )
        assert ctx.entry_bar["dir_coef"] == 0.5   # ts<=entry row (signal acted on)
        assert ctx.realized_bar["w_after_vol"] == 0.5  # ts>entry row (post-fill)


def test_build_context_entry_bar_is_nearest_ts_le_entry():
    """entry_bar = nearest ts <= entry_time (the signal the strategy acted on)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "state_trace.jsonl"
        _write_state_trace(p, [
            {"ts": "2020-06-30T00:00:00", "dir_coef": 0.5, "w_after_vol": 0.4},
            {"ts": "2020-07-02T00:00:00", "dir_coef": 1.0, "w_after_vol": 0.5},
        ])
        trace = load_state_trace(p)
        ctx = build_trade_context(
            entry_time="2020-07-01T00:00:00", exit_time="2020-07-05T00:00:00",
            symbol="518880", state_trace=trace, alpha_insights=[],
        )
        assert ctx.entry_bar["dir_coef"] == 0.5


def test_build_context_returns_none_entry_bar_when_no_state_trace():
    """No state_trace → entry_bar=None (adapter falls back to residual §3.5)."""
    ctx = build_trade_context(
        entry_time="2020-07-01T00:00:00", exit_time="2020-07-05T00:00:00",
        symbol="518880", state_trace=None, alpha_insights=[],
    )
    assert ctx.entry_bar is None


def test_load_closed_trades_maps_direction():
    """closedTrade.direction: 0=Long, 1=Short (LEAN TradeDirection enum)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "algo.json"
        p.write_text(json.dumps({
            "totalPerformance": {"closedTrades": [
                {"id": 1, "symbols": [{"value": "518880"}], "entryTime": "2020-07-01",
                 "entryPrice": "10.0", "direction": 0, "quantity": "100",
                 "exitTime": "2020-07-05", "exitPrice": "11.0",
                 "profitLoss": "100.0", "totalFees": "5.0",
                 "mae": "0.0", "mfe": "1.5", "duration": "4.00:00:00",
                 "endTradeDrawdown": "0.0", "isWin": True, "orderIds": [1]},
            ]}
        }))
        trades = load_closed_trades(p)
        assert len(trades) == 1
        assert trades[0].side == "long"
        assert trades[0].quantity == Decimal("100")
        assert trades[0].profit_loss == Decimal("100.0")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_no_lookahead.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'artifacts'`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/review/artifacts.py`:
```python
"""Load LEAN backtest artifacts + build TradeContext (no-lookahead). Spec §1.4, §3.4.

Reads {algo}.json (closedTrades, charts, orders), {algo}-order-events.json,
optional alpha-results.json, optional state_trace*.jsonl.
"""
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .adapters.base import TradeRecord, TradeContext

_TRADE_DIRECTION = {0: "long", 1: "short"}


def load_closed_trades(algo_json_path) -> list[TradeRecord]:
    """Load totalPerformance.closedTrades → list[TradeRecord]. Spec §2.3."""
    data = json.loads(Path(algo_json_path).read_text())
    out = []
    for ct in data.get("totalPerformance", {}).get("closedTrades", []):
        symbols = ct.get("symbols", [])
        sym = symbols[0]["value"] if symbols else ""
        out.append(TradeRecord(
            symbol=sym,
            entry_time=ct["entryTime"],
            entry_price=Decimal(str(ct["entryPrice"])),
            exit_time=ct["exitTime"],
            exit_price=Decimal(str(ct["exitPrice"])),
            quantity=Decimal(str(ct["quantity"])),
            side=_TRADE_DIRECTION.get(int(ct.get("direction", 0)), "long"),
            profit_loss=Decimal(str(ct["profitLoss"])),  # STRING → Decimal
            fees=Decimal(str(ct["totalFees"])),
            tpv_entry=Decimal("0"),  # filled by caller from state_trace or equity curve
            order_ids=list(ct.get("orderIds", [])),
        ))
    return out


def load_state_trace(path) -> list[dict]:
    """Load state_trace*.jsonl → chronological list of dicts. None if path missing."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    rows = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _parse_ts(s: str):
    """Parse ISO-8601 (with or without timezone). Trims to seconds for compare."""
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    return datetime.fromisoformat(s[:19])


def build_trade_context(entry_time: str, exit_time: str, symbol: str,
                        state_trace, alpha_insights) -> TradeContext:
    """Build TradeContext with no-lookahead (spec §3.4 C3):
    - entry_bar = latest state_trace row with ts <= entry_time (signal acted on)
    - realized_bar = first row with ts > entry_time AND ts <= exit_time (post-fill weight)
    """
    if not state_trace:
        return TradeContext(entry_bar=None, realized_bar=None)

    entry_dt = _parse_ts(entry_time)
    exit_dt = _parse_ts(exit_time)

    entry_bar = None
    realized_bar = None
    for row in state_trace:
        try:
            ts = _parse_ts(row.get("ts", ""))
        except (ValueError, TypeError):
            continue
        if ts <= entry_dt:
            entry_bar = row  # keep the latest ts <= entry
        if ts > entry_dt and ts <= exit_dt and realized_bar is None:
            realized_bar = row  # first post-fill row
            break
    return TradeContext(entry_bar=entry_bar, realized_bar=realized_bar)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_no_lookahead.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/artifacts.py Tests/test_review_no_lookahead.py
git commit -m "feat(review): artifact loading + TradeContext (no-lookahead ts>entry for realized weight)"
```

---

## Task 8: run_review.py CLI 主流程

**Files:**
- Create: `Scripts/review/cli.py`
- Create: `Scripts/review/run_review.py`
- Test: `Tests/test_review_cli.py`

> **关键**:`tpv_entry` 必须从 state_trace entry_bar 的 `tpv` 字段回填,否则 telescoping scale=0 导致 sum=0 ≠ profit_loss。`load_closed_trades` stub 填 `Decimal("0")`,`run()` 里从 `state_trace` 的 entry_bar `tpv` 或 equity curve 回填。

- [ ] **Step 1: Write the failing test**

`Tests/test_review_cli.py`:
```python
"""End-to-end CLI test on real gold2 artifacts. Spec §5.2, §6.1."""
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
RUN_REVIEW = _REPO / "Scripts" / "review" / "run_review.py"
MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"
RESULTS = _REPO / "Results" / "gold2-betavol"


def _run(args):
    return subprocess.run(
        [sys.executable, str(RUN_REVIEW)] + args,
        capture_output=True, text=True, cwd=str(_REPO),
    )


def test_cli_exit_0_on_real_gold2_artifacts():
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 backtest artifacts not present")
    r = _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    assert r.returncode in (0, 2), f"stdout={r.stdout[-500:]}\nstderr={r.stderr[-500:]}"


def test_cli_writes_review_json():
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 backtest artifacts not present")
    _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    review_json = RESULTS / "review" / "review.json"
    assert review_json.exists(), "review.json not written"
    doc = json.loads(review_json.read_text())
    assert "run_meta" in doc and "layer_attribution" in doc and "per_trade_narrative" in doc
    assert doc["run_meta"]["strategy_name"] == "Gold2BetaVolTargetStrategy"


def test_cli_exit_3_on_missing_hard_artifacts():
    """order-events.json + {algo}.json are hard-required; missing → exit 3."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = _run(["--manifest", str(MANIFEST), "--results-dir", d])
    assert r.returncode == 3, f"expected 3, got {r.returncode}. stderr={r.stderr[-300:]}"


def test_cli_layer_attribution_sums_to_total_pnl():
    """Spec §2.2 invariant: sum(layer pnl_abs) == decimal.Parse(totalProfitLoss)."""
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 backtest artifacts not present")
    _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    doc = json.loads((RESULTS / "review" / "review.json").read_text())
    from decimal import Decimal
    total = Decimal(doc["run_meta"]["total_closed_trade_pnl"])
    layer_sum = sum(Decimal(v["pnl_abs"]) for v in doc["layer_attribution"].values())
    assert abs(layer_sum - total) < Decimal("0.01"), \
        f"sum {layer_sum} != total {total} (gap {layer_sum - total})"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_cli.py -v
```
Expected: FAIL — `run_review.py` not found.

- [ ] **Step 3: Write minimal implementation**

`Scripts/review/cli.py`:
```python
"""run_review CLI: load manifest → adapter → 4 blocks → review.json. Spec §5.2."""
import importlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # Scripts/review on path
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))

from manifest_loader import load_manifest  # noqa: E402
from adapters.base import ReviewResult  # noqa: E402
from artifacts import load_closed_trades, load_state_trace, build_trade_context  # noqa: E402

SCHEMA_VERSION = "1"
ADAPTER_VERSION = "1.0.0"


def _find_artifact(results_dir: Path, stem: str, suffix: str):
    cands = list(results_dir.glob(f"{stem}{suffix}")) + list(results_dir.glob(f"*{suffix}"))
    return cands[0] if cands else None


def run(manifest_path: str, results_dir: str, write_html=False, write_influx=False,
        write_manifest=False) -> int:
    results_dir = Path(results_dir)
    manifest = load_manifest(manifest_path)
    review_cfg = manifest.raw.get("review", {})
    if not review_cfg:
        print("[review] no review: block in manifest; nothing to do", file=sys.stderr)
        return 0

    algo_json = _find_artifact(results_dir, manifest.strategy_name, ".json")
    order_events = _find_artifact(results_dir, manifest.strategy_name, "-order-events.json")
    if not algo_json or not order_events:
        print(f"[review] hard-required artifact missing (algo.json={algo_json}, "
              f"order-events={order_events})", file=sys.stderr)
        return 3

    mod = importlib.import_module(review_cfg["adapter_module"])
    adapter = getattr(mod, review_cfg["adapter_class"])()

    algo_data = json.loads(algo_json.read_text())
    trades = load_closed_trades(algo_json)
    tp = algo_data.get("totalPerformance", {})
    ts = tp.get("tradeStatistics", {})
    total_pnl = Decimal(str(ts.get("totalProfitLoss", "0")))  # STRING → Decimal

    # optional state_trace
    state_trace = None
    st_paths = list(results_dir.glob("state_trace*.jsonl"))
    if st_paths:
        state_trace = load_state_trace(st_paths[0])

    # 4 blocks
    layer_agg = {ln: {"pnl_abs": Decimal("0"), "n_trades": 0, "n_wins": 0}
                 for ln in adapter.LAYERS}
    per_trade = []
    for ct in trades:
        ctx = build_trade_context(ct.entry_time, ct.exit_time, ct.symbol, state_trace, [])
        ctx.entry_signal = {}  # alpha join留作 enrichment(Task 14 e2e 不依赖)
        ctx.regime_at_entry = ctx.entry_bar or {}
        # backfill tpv_entry from state_trace entry_bar tpv (else telescoping scale=0)
        if ctx.entry_bar and "tpv" in ctx.entry_bar:
            ct.tpv_entry = Decimal(str(ctx.entry_bar["tpv"]))
        try:
            layers = adapter.layer_attribution(ct, ctx)
            for ln, v in layers.items():
                layer_agg[ln]["pnl_abs"] += v
                layer_agg[ln]["n_trades"] += 1
                if v > 0:
                    layer_agg[ln]["n_wins"] += 1
        except ValueError:
            layer_agg[adapter.LAYERS[0]]["pnl_abs"] += ct.profit_loss
            layer_agg[adapter.LAYERS[0]]["n_trades"] += 1
        per_trade.append({
            "trade_id": ct.order_ids[0] if ct.order_ids else len(per_trade) + 1,
            "symbol": ct.symbol, "entry_time": ct.entry_time, "exit_time": ct.exit_time,
            "entry_price": str(ct.entry_price), "exit_price": str(ct.exit_price),
            "direction": ct.side, "quantity": str(ct.quantity),
            "pnl": str(ct.profit_loss), "fees": str(ct.fees),
            "mae": "0", "mfe": "0", "end_trade_drawdown": "0",
            "days_held": 0, "layer_contributions": {},
        })

    layer_attribution = {}
    for ln, agg in layer_agg.items():
        pct = float(agg["pnl_abs"] / total_pnl) if total_pnl != 0 else 0.0
        layer_attribution[ln] = {
            "pnl_abs": str(agg["pnl_abs"]),
            "pnl_pct_of_total": pct,
            "n_trades": agg["n_trades"],
            "n_wins": agg["n_wins"],
            "contribution_to_total_return": pct,
        }

    result = ReviewResult(
        run_meta={
            "strategy_name": manifest.strategy_name,
            "backtest_id": manifest.strategy_name,
            "period_start": ts.get("startDateTime", ""),
            "period_end": ts.get("endDateTime", ""),
            "total_closed_trade_pnl": str(total_pnl),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "adapter_version": ADAPTER_VERSION,
            "schema_version": SCHEMA_VERSION,
        },
        layer_attribution=layer_attribution,
        per_trade_narrative=per_trade,
        drawdown_attribution=[],  # Task 9 fills
        tca=None,  # Task 9 fills
    )

    import jsonschema
    schema = json.loads((Path(__file__).parent / "schema" / "review_schema.json").read_text())
    doc = _result_to_dict(result)
    jsonschema.validate(doc, schema)

    out_dir = results_dir / "review"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review.json").write_text(json.dumps(doc, indent=2, default=str))
    print(f"[review] wrote {out_dir / 'review.json'}")
    return 0


def _result_to_dict(result: ReviewResult) -> dict:
    return {
        "run_meta": result.run_meta,
        "layer_attribution": result.layer_attribution,
        "per_trade_narrative": result.per_trade_narrative,
        "drawdown_attribution": result.drawdown_attribution,
        "tca": result.tca,
    }
```

`Scripts/review/run_review.py`:
```python
#!/usr/bin/env python3
"""run_review CLI entrypoint. Spec §5.2. Usage:
    python3 Scripts/review/run_review.py --manifest <manifest.yaml> --results-dir <Results/<run>>
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli import run  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Trading-review (复盘) layer. Spec 2026-07-10.")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--influx", action="store_true")
    ap.add_argument("--write-manifest", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.manifest, args.results_dir, args.html, args.influx, args.write_manifest))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_cli.py -v
```
Expected: PASS (4 tests). `test_cli_layer_attribution_sums_to_total_pnl` is the critical invariant — if gold2 has no state_trace (current state), adapter runs residual mode: trend=PL, others=0, sum=PL=total ✓. If state_trace exists after Task 3 backtest, telescoping runs with tpv backfilled.

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/cli.py Scripts/review/run_review.py Tests/test_review_cli.py
git commit -m "feat(review): run_review.py CLI — manifest→adapter→4 blocks→review.json + tpv backfill"
```

---

## Task 9: drawdown_attribution + tca blocks

**Files:**
- Modify: `Scripts/review/cli.py`
- Test: `Tests/test_review_cli.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `Tests/test_review_cli.py`:
```python
def test_cli_drawdown_attribution_populated():
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 backtest artifacts not present")
    _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    doc = json.loads((RESULTS / "review" / "review.json").read_text())
    assert isinstance(doc["drawdown_attribution"], list)
    assert len(doc["drawdown_attribution"]) > 0, "drawdown attribution empty"
    dd = doc["drawdown_attribution"][0]
    assert "peak_time" in dd and "trough_time" in dd and "depth_pct" in dd
    assert dd["depth_pct"] < 0  # drawdown is negative


def test_cli_tca_populated_from_orderSubmissionData():
    """Spec §2.5: tca from order-events.fillPrice joined to orders.orderSubmissionData.
    Verified 997/997 orders have orderSubmissionData → tca must be non-null."""
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 backtest artifacts not present")
    _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    doc = json.loads((RESULTS / "review" / "review.json").read_text())
    assert doc["tca"] is not None, "tca should be populated (orderSubmissionData present)"
    assert doc["tca"]["n_fills"] > 0
    assert "avg_slippage_bps" in doc["tca"]
    assert doc["tca"]["source"] == "orderSubmissionData"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_cli.py::test_cli_drawdown_attribution_populated Tests/test_review_cli.py::test_cli_tca_populated_from_orderSubmissionData -v
```
Expected: FAIL — drawdown_attribution empty `[]`, tca null.

- [ ] **Step 3: Write minimal implementation**

Add to `Scripts/review/cli.py` two helper functions:
```python
def _drawdown_attribution(algo_data: dict) -> list:
    """Top-N drawdown episodes from charts['Drawdown'].values [[ts, depth], ...]. Spec §2.4."""
    charts = algo_data.get("charts", {})
    dd_series = charts.get("Drawdown", {}).get("series", {}).get("Equity Drawdown", {}).get("values", [])
    if not dd_series:
        return []
    episodes = []
    in_dd = False
    peak_ts = trough_ts = None
    trough_depth = 0.0
    for ts, depth in dd_series:
        if depth < 0 and not in_dd:
            in_dd = True
            peak_ts = ts
            trough_ts = ts
            trough_depth = depth
        elif in_dd:
            if depth < trough_depth:
                trough_depth = depth
                trough_ts = ts
            if depth >= 0:
                episodes.append({
                    "peak_time": _ts_to_iso(peak_ts),
                    "trough_time": _ts_to_iso(trough_ts),
                    "recovery_time": _ts_to_iso(ts),
                    "depth_pct": trough_depth,
                    "top_contributing_layers": [],
                    "top_contributing_trades": [],
                    "regime_during": None,
                    "regime_source": "none",
                })
                in_dd = False
    episodes.sort(key=lambda e: e["depth_pct"])
    return episodes[:5]


def _ts_to_iso(ts) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tca(algo_data: dict, order_events_path: Path) -> dict:
    """Slippage = mean over filled events of (fillPrice-mid)/mid*10000. Spec §2.5."""
    orders = algo_data.get("orders", {})
    oe = json.loads(order_events_path.read_text()) if order_events_path.exists() else []
    slips = []
    n_fills = 0
    for ev in oe:
        if ev.get("status") != "Filled":
            continue
        n_fills += 1
        oid = str(ev.get("orderId"))
        order = orders.get(oid, {})
        osd = order.get("orderSubmissionData", {})
        bid = osd.get("bidPrice")
        ask = osd.get("askPrice")
        last = osd.get("lastPrice")
        fill = ev.get("fillPrice")
        if fill is None:
            continue
        mid = None
        if bid is not None and ask is not None and float(bid) > 0 and float(ask) > 0:
            mid = (float(bid) + float(ask)) / 2
        elif last is not None and float(last) > 0:
            mid = float(last)
        if mid and mid > 0:
            slips.append((float(fill) - mid) / mid * 10000)
    if not slips:
        return {"avg_slippage_bps": None, "fill_quality_score": None, "n_fills": n_fills,
                "source": "absent:orderSubmissionData" if n_fills == 0 else "absent:no mid"}
    avg = sum(slips) / len(slips)
    return {"avg_slippage_bps": avg, "fill_quality_score": 1.0 - min(abs(avg) / 100, 1.0),
            "n_fills": n_fills, "source": "orderSubmissionData"}
```

Then in `run()`, replace the empty `drawdown_attribution=[]` and `tca=None` with:
```python
        drawdown_attribution=_drawdown_attribution(algo_data),
        tca=_tca(algo_data, order_events),
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_cli.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/cli.py Tests/test_review_cli.py
git commit -m "feat(review): drawdown_attribution + tca blocks (orderSubmissionData slippage)"
```

---

## Task 10: HTML tearsheet 生成器

**Files:**
- Create: `Scripts/review/tearsheet/__init__.py`
- Create: `Scripts/review/tearsheet/builder.py`
- Create: `Scripts/review/tearsheet/templates/__init__.py`
- Create: `Scripts/review/tearsheet/templates/gold2.html.j2`
- Test: `Tests/test_review_tearsheet.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_review_tearsheet.py`:
```python
"""Tests for HTML tearsheet builder. Spec §4.1, §6.1."""
import json
import sys
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
from tearsheet.builder import build_html  # noqa: E402


def _doc():
    return {
        "run_meta": {"strategy_name": "Gold2BetaVolTargetStrategy", "backtest_id": "x",
                     "period_start": "2020", "period_end": "2026",
                     "total_closed_trade_pnl": "100.0", "generated_at": "now",
                     "adapter_version": "1", "schema_version": "1"},
        "layer_attribution": {
            "trend": {"pnl_abs": "60.0", "pnl_pct_of_total": 0.6, "n_trades": 5, "n_wins": 3, "contribution_to_total_return": 0.06},
            "vol_target": {"pnl_abs": "40.0", "pnl_pct_of_total": 0.4, "n_trades": 5, "n_wins": 2, "contribution_to_total_return": 0.04},
            "extreme_risk": {"pnl_abs": "0.0", "pnl_pct_of_total": 0.0, "n_trades": 0, "n_wins": 0, "contribution_to_total_return": 0.0},
            "realrate_cap": {"pnl_abs": "0.0", "pnl_pct_of_total": 0.0, "n_trades": 0, "n_wins": 0, "contribution_to_total_return": 0.0},
        },
        "per_trade_narrative": [],
        "drawdown_attribution": [],
        "tca": None,
    }


def test_build_html_returns_string():
    html = build_html(_doc())
    assert isinstance(html, str)
    assert "<html" in html.lower()


def test_html_contains_layer_attribution_panel():
    html = build_html(_doc())
    assert "trend" in html and "vol_target" in html


def test_html_is_self_contained_no_external_deps():
    """No CDN, no external script/style links. Spec §4.1."""
    html = build_html(_doc())
    assert "cdn" not in html.lower()
    assert "https://" not in html
    assert "plotly" not in html.lower()


def test_self_check_sum_invariant():
    """--self-check: sum(layer pnl_abs) == total_closed_trade_pnl. Spec §4.1 fail-fast."""
    import pytest
    doc = _doc()
    doc["layer_attribution"]["trend"]["pnl_abs"] = "999.0"  # break invariant
    with pytest.raises(AssertionError, match="sum"):
        build_html(doc, self_check=True)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_tearsheet.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'tearsheet.builder'`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/review/tearsheet/__init__.py`: `"""HTML tearsheet builder."""`

`Scripts/review/tearsheet/templates/__init__.py`: empty.

`Scripts/review/tearsheet/templates/gold2.html.j2`:
```html
<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>Review: {{ run_meta.strategy_name }}</title>
<style>
body{font-family:system-ui,sans-serif;margin:20px}
.bar{height:20px;display:inline-block;vertical-align:bottom}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:4px}
</style></head><body>
<h1>{{ run_meta.strategy_name }} — Review</h1>
<p>Period: {{ run_meta.period_start }} → {{ run_meta.period_end }} | Generated: {{ run_meta.generated_at }}</p>

<h2>Layer Attribution</h2>
{% for ln, v in layer_attribution.items() %}
<div>{{ ln }}: {{ v.pnl_abs }} ({{ (v.pnl_pct_of_total*100)|round(1) }}%)
<span class="bar" style="width:{{ (v.pnl_pct_of_total*200)|round(0) }}px;background:{% if v.pnl_abs|float > 0 %}green{% else %}red{% endif %}"></span>
</div>
{% endfor %}

<h2>Per-Trade Narrative</h2>
<table><tr><th>ID</th><th>Symbol</th><th>Entry</th><th>Exit</th><th>Dir</th><th>PnL</th><th>Fees</th></tr>
{% for t in per_trade_narrative %}
<tr><td>{{ t.trade_id }}</td><td>{{ t.symbol }}</td><td>{{ t.entry_time }}</td><td>{{ t.exit_time }}</td>
<td>{{ t.direction }}</td><td>{{ t.pnl }}</td><td>{{ t.fees }}</td></tr>
{% endfor %}
</table>

{% if tca %}
<h2>TCA</h2>
<p>avg_slippage_bps={{ tca.avg_slippage_bps }} | n_fills={{ tca.n_fills }} | source={{ tca.source }}</p>
{% endif %}

{% if drawdown_attribution %}
<h2>Drawdown Attribution</h2>
<table><tr><th>Peak</th><th>Trough</th><th>Depth</th></tr>
{% for d in drawdown_attribution %}
<tr><td>{{ d.peak_time }}</td><td>{{ d.trough_time }}</td><td>{{ (d.depth_pct*100)|round(2) }}%</td></tr>
{% endfor %}
</table>
{% endif %}
</body></html>
```

`Scripts/review/tearsheet/builder.py`:
```python
"""HTML tearsheet from review.json alone. Spec §4.1. Jinja2 + inline, no CDN/plotly."""
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TPL_DIR = Path(__file__).parent / "templates"


def build_html(review_doc: dict, self_check: bool = False) -> str:
    """Render self-contained HTML from a review.json dict."""
    if self_check:
        total = Decimal(str(review_doc["run_meta"]["total_closed_trade_pnl"]))
        layer_sum = sum(Decimal(str(v["pnl_abs"])) for v in review_doc["layer_attribution"].values())
        assert abs(layer_sum - total) < Decimal("0.01"), \
            f"sum invariant violated: sum={layer_sum} total={total}"
    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)), autoescape=False)
    tpl = env.get_template("gold2.html.j2")
    return tpl.render(**review_doc)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_tearsheet.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/tearsheet/__init__.py Scripts/review/tearsheet/builder.py Scripts/review/tearsheet/templates/__init__.py Scripts/review/tearsheet/templates/gold2.html.j2 Tests/test_review_tearsheet.py
git commit -m "feat(review): HTML tearsheet builder (Jinja2 + inline, self-contained, self-check)"
```

---

## Task 11: InfluxDB review_* 导出

**Files:**
- Create: `Scripts/review/influx_export.py`
- Test: `Tests/test_review_influx_export.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_review_influx_export.py`:
```python
"""Tests for review_* InfluxDB line-protocol export. Spec §4.2. Dry-run (no live InfluxDB)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
sys.path.insert(0, str(_REPO / "Scripts"))
from influx_export import build_lines  # noqa: E402


def _doc():
    return {
        "run_meta": {"strategy_name": "Gold2BetaVolTargetStrategy", "total_closed_trade_pnl": "100.0",
                     "period_end": "2026-06-23T00:00:00Z"},
        "layer_attribution": {
            "trend": {"pnl_abs": "60.0", "pnl_pct_of_total": 0.6, "n_trades": 5, "n_wins": 3, "contribution_to_total_return": 0.06},
        },
        "per_trade_narrative": [
            {"trade_id": 1, "symbol": "518880", "exit_time": "2020-07-05T00:00:00Z",
             "direction": "long", "pnl": "100.0", "mae": "0.0", "mfe": "1.5", "days_held": 4,
             "layer_contributions": {"trend": "100.0"}, "regime_at_entry": {}},
        ],
        "drawdown_attribution": [
            {"trough_time": "2021-03-01T00:00:00Z", "depth_pct": -0.15, "top_layer": "trend", "duration_days": 60},
        ],
        "tca": {"avg_slippage_bps": 1.2, "fill_quality_score": 0.95, "n_fills": 100},
    }


def test_build_lines_returns_review_layer_attribution():
    lines = build_lines(_doc(), algorithm_id="Gold2BetaVolTargetStrategy", mode="backtesting", run_id="r1")
    layer_lines = [l for l in lines if l.startswith("review_layer_attribution")]
    assert len(layer_lines) >= 1
    assert "algorithm_id=Gold2BetaVolTargetStrategy" in layer_lines[0]
    assert "layer=trend" in layer_lines[0]
    assert "pnl_abs=60" in layer_lines[0]


def test_build_lines_returns_review_trade():
    lines = build_lines(_doc(), algorithm_id="Gold2", mode="backtesting", run_id="r1")
    trade_lines = [l for l in lines if l.startswith("review_trade")]
    assert len(trade_lines) == 1
    assert "symbol=518880" in trade_lines[0]
    assert "direction=long" in trade_lines[0]
    assert "trend_contrib=100" in trade_lines[0]


def test_build_lines_returns_review_drawdown():
    lines = build_lines(_doc(), algorithm_id="Gold2", mode="backtesting", run_id="r1")
    dd_lines = [l for l in lines if l.startswith("review_drawdown")]
    assert len(dd_lines) == 1
    assert "depth_pct=" in dd_lines[0]
    assert "top_layer=trend" in dd_lines[0]


def test_build_lines_returns_review_tca():
    lines = build_lines(_doc(), algorithm_id="Gold2", mode="backtesting", run_id="r1")
    tca_lines = [l for l in lines if l.startswith("review_tca")]
    assert len(tca_lines) == 1
    assert "avg_slippage_bps=1.2" in tca_lines[0]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_influx_export.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'influx_export'`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/review/influx_export.py`:
```python
"""review_* InfluxDB line-protocol export. Spec §4.2. Reuses InfluxPoint + write_lines_to_influx
from Scripts/export_backtest_results_to_influx.py for escaping + HTTP write."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "Scripts"))
from export_backtest_results_to_influx import (  # noqa: E402
    InfluxPoint, point_to_line_protocol, write_lines_to_influx,
)


def _ts_to_ns(iso: str) -> int:
    """ISO-8601 string → nanoseconds since epoch."""
    s = iso.replace("Z", "+00:00") if iso.endswith("Z") else iso
    dt = datetime.fromisoformat(s[:19])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def build_lines(review_doc: dict, algorithm_id: str, mode: str, run_id: str) -> list[str]:
    """Build InfluxDB line-protocol lines for the 4 review_* measurements."""
    lines = []
    end_ns = _ts_to_ns(review_doc["run_meta"].get("period_end", datetime.now(timezone.utc).isoformat()))

    for layer, v in review_doc.get("layer_attribution", {}).items():
        lines.append(point_to_line_protocol(InfluxPoint(
            measurement="review_layer_attribution",
            tags={"algorithm_id": algorithm_id, "layer": layer, "mode": mode, "run_id": run_id},
            fields={"pnl_abs": float(v["pnl_abs"]), "pnl_pct_of_total": float(v["pnl_pct_of_total"]),
                    "n_trades": int(v["n_trades"]), "n_wins": int(v["n_wins"]),
                    "contribution_to_total_return": float(v["contribution_to_total_return"])},
            timestamp_ns=end_ns,
        )))

    for t in review_doc.get("per_trade_narrative", []):
        fields = {"pnl": float(t["pnl"]), "mae": float(t.get("mae", 0)), "mfe": float(t.get("mfe", 0)),
                  "days_held": int(t.get("days_held", 0))}
        for ln, contrib in t.get("layer_contributions", {}).items():
            fields[f"{ln}_contrib"] = float(contrib)
        regime = t.get("regime_at_entry", {}) or {}
        regime_tag = str(regime.get("regime", "unknown"))
        lines.append(point_to_line_protocol(InfluxPoint(
            measurement="review_trade",
            tags={"algorithm_id": algorithm_id, "symbol": t["symbol"], "regime_at_entry": regime_tag,
                  "direction": t["direction"], "run_id": run_id, "mode": mode},
            fields=fields,
            timestamp_ns=_ts_to_ns(t["exit_time"]),
        )))

    for d in review_doc.get("drawdown_attribution", []):
        top_layer = d["top_contributing_layers"][0]["layer"] if d.get("top_contributing_layers") else "none"
        lines.append(point_to_line_protocol(InfluxPoint(
            measurement="review_drawdown",
            tags={"algorithm_id": algorithm_id, "run_id": run_id, "mode": mode,
                  "regime_at_trough": str(d.get("regime_during") or "unknown")},
            fields={"depth_pct": float(d["depth_pct"]), "top_layer": top_layer,
                    "duration_days": int(d.get("duration_days", 0))},
            timestamp_ns=_ts_to_ns(d["trough_time"]),
        )))

    tca = review_doc.get("tca")
    if tca:
        lines.append(point_to_line_protocol(InfluxPoint(
            measurement="review_tca",
            tags={"algorithm_id": algorithm_id, "run_id": run_id, "mode": mode},
            fields={"avg_slippage_bps": float(tca["avg_slippage_bps"] or 0),
                    "fill_quality_score": float(tca["fill_quality_score"] or 0),
                    "n_fills": int(tca["n_fills"])},
            timestamp_ns=end_ns,
        )))
    return lines


def export(review_doc: dict, algorithm_id: str, mode: str = "backtesting",
           run_id: str = None, dry_run: bool = False) -> int:
    """Write review_* to InfluxDB. Env: INFLUXDB_URL/ORG/BUCKET/TOKEN."""
    lines = build_lines(review_doc, algorithm_id, mode, run_id or "manual")
    if dry_run:
        for l in lines[:5]:
            print(l)
        print(f"... ({len(lines)} total)")
        return len(lines)
    return write_lines_to_influx(
        lines,
        influx_url=os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086"),
        org=os.environ.get("INFLUXDB_ORG", "lean"),
        bucket=os.environ.get("INFLUXDB_BUCKET", "quant"),
        token=os.environ.get("INFLUXDB_TOKEN", "admin-token-leansystem"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_influx_export.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/influx_export.py Tests/test_review_influx_export.py
git commit -m "feat(review): InfluxDB review_* export (4 measurements, reuses InfluxPoint)"
```

---

## Task 12: Grafana strategy-review dashboard + regression test

**Files:**
- Create: `monitoring/grafana/dashboards/lean/strategy-review.json`
- Create: `Scripts/_strategy_review_baseline_6.json`
- Create: `Scripts/test_strategy_review_dashboard_regression.py`

- [ ] **Step 1: Write the failing test**

`Scripts/test_strategy_review_dashboard_regression.py`:
```python
"""Regression test: strategy-review dashboard's 6 panels must never change. Spec §4.3."""
import json
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "monitoring" / "grafana" / "dashboards" / "lean" / "strategy-review.json"
BASELINE = Path(__file__).resolve().parent / "_strategy_review_baseline_6.json"
ALLOWED_TYPES = {"stat", "barchart", "scatter", "table", "timeseries", "bargauge", "row", "gauge"}


def _panel_fingerprint(p: dict) -> tuple:
    gp = p.get("gridPos", {})
    return (p.get("title", ""), (gp.get("h"), gp.get("w"), gp.get("x"), gp.get("y")), p.get("type", ""))


def test_dashboard_exists():
    assert DASHBOARD.exists(), f"missing {DASHBOARD}"


def test_original_6_panels_unchanged():
    db = json.loads(DASHBOARD.read_text())
    panels = [p for p in db.get("panels", []) if p.get("type") != "row"]
    current = [_panel_fingerprint(p) for p in panels]
    baseline = json.loads(BASELINE.read_text())
    normalized_baseline = [(b[0], tuple(b[1]), b[2]) for b in baseline]
    assert current == normalized_baseline, (
        f"panel layout changed!\ncurrent={current}\nbaseline={normalized_baseline}"
    )


def test_algorithm_id_variable_preserved():
    db = json.loads(DASHBOARD.read_text())
    vars_ = {v.get("name"): v.get("query", "") for v in db.get("templating", {}).get("list", [])}
    assert "algorithm_id" in vars_, "algorithm_id template var missing"
    assert "review_layer_attribution" in vars_["algorithm_id"]


def test_no_stale_plugins():
    db = json.loads(DASHBOARD.read_text())
    for p in db.get("panels", []):
        assert p.get("type") in ALLOWED_TYPES, f"stale plugin type: {p.get('type')}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Scripts/test_strategy_review_dashboard_regression.py -v
```
Expected: FAIL — dashboard file missing.

- [ ] **Step 3: Write minimal implementation**

`monitoring/grafana/dashboards/lean/strategy-review.json`:
```json
{
  "schemaVersion": 39,
  "uid": "strategy-review",
  "tags": ["lean", "review"],
  "title": "Strategy Review (复盘)",
  "timezone": "browser",
  "templating": {
    "list": [
      {"name": "algorithm_id", "type": "query", "datasource": {"type": "influxdb", "uid": "lean-influxdb"},
       "query": "SHOW TAG VALUES FROM \"review_layer_attribution\" WITH KEY = \"algorithm_id\""},
      {"name": "run_id", "type": "query", "datasource": {"type": "influxdb", "uid": "lean-influxdb"},
       "query": "SHOW TAG VALUES FROM \"review_layer_attribution\" WITH KEY = \"run_id\""},
      {"name": "mode", "type": "query", "datasource": {"type": "influxdb", "uid": "lean-influxdb"},
       "query": "SHOW TAG VALUES FROM \"review_layer_attribution\" WITH KEY = \"mode\"", "current": {"text": "backtesting", "value": "backtesting"}}
    ]
  },
  "panels": [
    {"title": "Review Status", "type": "stat", "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
     "datasource": {"type": "influxdb", "uid": "lean-influxdb"},
     "targets": [{"query": "SELECT count(depth_pct) FROM \"review_drawdown\" WHERE \"algorithm_id\"='${algorithm_id}' AND $timeFilter"}]},
    {"title": "Layer Attribution", "type": "barchart", "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8},
     "datasource": {"type": "influxdb", "uid": "lean-influxdb"},
     "targets": [{"query": "SELECT sum(pnl_abs) FROM \"review_layer_attribution\" WHERE \"algorithm_id\"='${algorithm_id}' AND \"run_id\"='${run_id}' GROUP BY layer"}]},
    {"title": "Per-Trade pnl vs mae", "type": "scatter", "gridPos": {"h": 8, "w": 6, "x": 0, "y": 16},
     "datasource": {"type": "influxdb", "uid": "lean-influxdb"},
     "targets": [{"query": "SELECT pnl,mae FROM \"review_trade\" WHERE \"algorithm_id\"='${algorithm_id}' GROUP BY \"regime_at_entry\""}]},
    {"title": "Drawdown Attribution", "type": "table", "gridPos": {"h": 8, "w": 6, "x": 6, "y": 16},
     "datasource": {"type": "influxdb", "uid": "lean-influxdb"},
     "targets": [{"query": "SELECT depth_pct,top_layer,duration_days FROM \"review_drawdown\" WHERE \"algorithm_id\"='${algorithm_id}' ORDER BY time DESC"}]},
    {"title": "Cumulative Return by Regime", "type": "timeseries", "gridPos": {"h": 8, "w": 12, "x": 0, "y": 24},
     "datasource": {"type": "influxdb", "uid": "lean-influxdb"},
     "targets": [{"query": "SELECT \"value\" FROM \"lean_chart\" WHERE \"chart\"='Strategy Equity' AND \"algorithm_id\"='${algorithm_id}'"}]},
    {"title": "Layer PnL %", "type": "bargauge", "gridPos": {"h": 8, "w": 12, "x": 0, "y": 32},
     "datasource": {"type": "influxdb", "uid": "lean-influxdb"},
     "targets": [{"query": "SELECT last(pnl_pct_of_total) FROM \"review_layer_attribution\" WHERE \"algorithm_id\"='${algorithm_id}' GROUP BY layer"}]}
  ]
}
```

`Scripts/_strategy_review_baseline_6.json`:
```json
[
  ["Review Status", [8, 12, 0, 0], "stat"],
  ["Layer Attribution", [8, 12, 0, 8], "barchart"],
  ["Per-Trade pnl vs mae", [8, 6, 0, 16], "scatter"],
  ["Drawdown Attribution", [8, 6, 6, 16], "table"],
  ["Cumulative Return by Regime", [8, 12, 0, 24], "timeseries"],
  ["Layer PnL %", [8, 12, 0, 32], "bargauge"]
]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Scripts/test_strategy_review_dashboard_regression.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add monitoring/grafana/dashboards/lean/strategy-review.json Scripts/_strategy_review_baseline_6.json Scripts/test_strategy_review_dashboard_regression.py
git commit -m "feat(review): Grafana strategy-review dashboard (6 panels) + regression test"
```

---

## Task 13: pipeline overlay + manifest_lint warnings + e2e/scheduler 接线

**Files:**
- Create: `Scripts/review/pipeline_overlay.py`
- Modify: `Scripts/auto_optimize/manifest_lint.py`
- Modify: `Scripts/auto_optimize/run_e2e.py`
- Modify: `Scripts/auto_optimize/evolution_scheduler.py`
- Test: `Tests/test_review_pipeline_overlay.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_review_pipeline_overlay.py`:
```python
"""Tests for pipeline overlay + manifest_lint warnings. Spec §1.5, §5.3, §5.4."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from pipeline_overlay import should_run  # noqa: E402
from manifest_loader import load_manifest  # noqa: E402
from manifest_lint import lint_manifest  # noqa: E402

MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"


def test_should_run_manual_returns_false():
    m = load_manifest(MANIFEST)
    m.raw["review"]["review_schedule"] = "manual"
    assert should_run(m) is False


def test_should_run_on_backtest_returns_true():
    m = load_manifest(MANIFEST)
    m.raw["review"]["review_schedule"] = "on_backtest"
    assert should_run(m) is True


def test_should_run_no_review_block_returns_false():
    class FakeM:
        raw = {}
    assert should_run(FakeM()) is False


def test_manifest_lint_has_warnings_field():
    """Spec §5.4: LintResult gains warnings list; review-not-run → warning, not error."""
    m = load_manifest(MANIFEST)
    result = lint_manifest(m, code_params={"trend-disable"}, state_fields={"ts"})
    assert hasattr(result, "warnings")
    assert isinstance(result.warnings, list)
    assert result.ok == (len(result.errors) == 0)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_pipeline_overlay.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline_overlay'` / `LintResult` no `warnings`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/review/pipeline_overlay.py`:
```python
"""Optional pipeline stage. Spec §1.5, §5.3. NEVER blocks deploy_gate by default."""
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
RUN_REVIEW = Path(__file__).resolve().parent / "run_review.py"


def should_run(manifest) -> bool:
    """review_schedule != 'manual' and review: block present → run."""
    r = (getattr(manifest, "raw", {}) or {}).get("review", {})
    if not r:
        return False
    return r.get("review_schedule", "manual") != "manual"


def run_if_scheduled(manifest_path: str, results_dir: str, manifest) -> bool:
    """Invoke run_review.py non-blocking. Returns True if ran. Spec §5.3."""
    if not should_run(manifest):
        return False
    try:
        subprocess.run(
            [sys.executable, str(RUN_REVIEW), "--manifest", manifest_path,
             "--results-dir", results_dir, "--write-manifest"],
            cwd=str(_REPO), capture_output=True, text=True, timeout=600,
        )
        return True
    except Exception as e:
        print(f"[review-overlay] non-blocking failure: {e}", file=sys.stderr)
        return False
```

Modify `Scripts/auto_optimize/manifest_lint.py` — replace the dataclass import and `LintResult`:
```python
from dataclasses import dataclass, field
from manifest_loader import StrategyManifest


@dataclass
class LintResult:
    ok: bool
    errors: list
    warnings: list = field(default_factory=list)   # Spec §5.4: advisory, non-gate-failing


def lint_manifest(manifest: StrategyManifest, code_params: set, state_fields: set) -> LintResult:
    errors = []
    warnings = []
    completeness = getattr(manifest, "rl_state_completeness", "unaudited")
    if completeness in ("partial", "unaudited"):
        errors.append(
            f"rl_state_completeness='{completeness}' — 策略不允许进入灰度部署 "
            f"(state 字段未全部填真实值或未审计); 请补全 SerializeRlState 真实字段后改 'full'")
    for p in manifest.parameter_space:
        if p.name not in code_params:
            errors.append(f"parameter '{p.name}' declared in manifest but not found in strategy code")
    manifest_fields = {f.name for f in manifest.state_schema.fields}
    for f in manifest_fields - state_fields:
        errors.append(f"state field '{f}' declared in manifest but not produced by SerializeRlState()")
    for f in state_fields - manifest_fields:
        errors.append(f"state field '{f}' produced by SerializeRlState() but not in manifest")
    # NEW: review-not-run advisory warning (not an error)
    review = getattr(manifest, "raw", {}).get("review", {})
    if not review:
        warnings.append("no review: block in manifest — trading-review (复盘) not configured")
    return LintResult(ok=len(errors) == 0, errors=errors, warnings=warnings)
```

Modify `Scripts/auto_optimize/run_e2e.py` — replace `e2e()` body:
```python
import argparse, json, pathlib, subprocess
from pathlib import Path
from manifest_loader import load_manifest
from bayesian_optimizer import optimize
from overfitting_report import generate_report
from baseline_test import run_baseline_test

def e2e(manifest_path: str, config: dict):
    manifest = load_manifest(manifest_path)
    # Layer A
    a_result = optimize(manifest_path, config, n_trials=config["optuna"]["n_trials"])
    trace_path = f"Results/auto_optimize/{manifest.strategy_name}/state_trace.jsonl"
    report = generate_report(manifest.strategy_name, a_result["n_trials"],
                             dsr_is=a_result["best_value"], dsr_oos_mean=a_result["best_value"]*0.66,
                             dsr_oos_std=0.1, ridge_converged=a_result["ridge_converged"])
    baseline = run_baseline_test({"dsr": a_result["best_value"]}, {"dsr": 0}, {"dsr": 0})
    # Review overlay (spec §5.3): non-blocking, gated by review.review_schedule
    review_result = None
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "review"))
        from pipeline_overlay import run_if_scheduled
        results_dir = str(Path(__file__).resolve().parents[2] / "Results" / manifest.strategy_name)
        ran = run_if_scheduled(manifest_path, results_dir, manifest)
        review_result = {"ran": ran}
    except Exception as e:
        review_result = {"ran": False, "error": str(e)}
    return {"layer_a": a_result, "overfitting": report, "baseline": baseline, "review": review_result}

if __name__ == "__main__":
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()
    config = yaml.safe_load(open("Scripts/auto_optimize/config.yaml"))
    print(json.dumps(e2e(args.manifest, config), indent=2, default=str))
```

Modify `Scripts/auto_optimize/evolution_scheduler.py` — in `fire_optimization`, after line 130 (`print(f"  ✅ 优化完成...")`), before line 131 (`print(f"  ⚠️ deploy_gate...")`):
```python
    # Review overlay (spec §1.5, §5.3): weekly cadence, non-blocking.
    try:
        import sys as _sys
        _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "review"))
        from pipeline_overlay import run_if_scheduled
        from manifest_loader import load_manifest as _lm
        m = _lm(manifest_path)
        results_dir = str(_REPO_ROOT / "Results" / m.strategy_name)
        run_if_scheduled(manifest_path, results_dir, m)
    except Exception as ex:
        print(f"  [review-overlay] non-blocking failure: {ex}")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_pipeline_overlay.py Scripts/test_strategy_review_dashboard_regression.py Tests/test_review_cli.py -v 2>&1 | tail -20
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/pipeline_overlay.py Scripts/auto_optimize/manifest_lint.py Scripts/auto_optimize/run_e2e.py Scripts/auto_optimize/evolution_scheduler.py Tests/test_review_pipeline_overlay.py
git commit -m "feat(review): pipeline overlay + manifest_lint warnings + e2e/scheduler hooks (non-blocking)"
```

---

## Task 14: 端到端集成验证(--html/--influx 接线)

**Files:**
- Modify: `Scripts/review/cli.py`
- Test: `Tests/test_review_e2e_integration.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_review_e2e_integration.py`:
```python
"""End-to-end integration: run_review on real gold2 → review.json → HTML → InfluxDB lines.
Spec §6.1. Verifies the full stack works together on real artifacts."""
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
RUN_REVIEW = _REPO / "Scripts" / "review" / "run_review.py"
MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"
RESULTS = _REPO / "Results" / "gold2-betavol"


def _run(args):
    return subprocess.run([sys.executable, str(RUN_REVIEW)] + args,
                          capture_output=True, text=True, cwd=str(_REPO))


def test_e2e_full_stack_on_gold2():
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 artifacts absent")
    r = _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS), "--html"])
    assert r.returncode in (0, 2), f"stderr={r.stderr[-800:]}"

    review_json = RESULTS / "review" / "review.json"
    review_html = RESULTS / "review" / "review.html"
    assert review_json.exists()
    assert review_html.exists(), "HTML tearsheet not written"

    doc = json.loads(review_json.read_text())
    total = Decimal(doc["run_meta"]["total_closed_trade_pnl"])
    layer_sum = sum(Decimal(v["pnl_abs"]) for v in doc["layer_attribution"].values())
    assert abs(layer_sum - total) < Decimal("0.01"), f"sum {layer_sum} != total {total}"

    assert set(doc["layer_attribution"].keys()) == {"trend", "vol_target", "extreme_risk", "realrate_cap"}
    assert doc["tca"] is not None
    assert doc["tca"]["n_fills"] > 0

    html = review_html.read_text()
    assert "https://" not in html
    assert "cdn" not in html.lower()


def test_e2e_influx_lines_build():
    """InfluxDB lines build without a live InfluxDB (dry-run)."""
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 artifacts absent")
    _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    sys.path.insert(0, str(_REPO / "Scripts" / "review"))
    sys.path.insert(0, str(_REPO / "Scripts"))
    doc = json.loads((RESULTS / "review" / "review.json").read_text())
    from influx_export import build_lines
    lines = build_lines(doc, "Gold2BetaVolTargetStrategy", "backtesting", "e2e-test")
    assert any(l.startswith("review_layer_attribution") for l in lines)
    assert any(l.startswith("review_trade") for l in lines)
    assert any(l.startswith("review_tca") for l in lines)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_e2e_integration.py -v
```
Expected: FAIL — `--html` flag not wired into `run()` (cli.py ignores write_html; review.html not written).

- [ ] **Step 3: Wire --html and --influx into cli.py run()**

In `Scripts/review/cli.py` `run()`, refactor so `doc` is computed once and reused. After the existing `jsonschema.validate(doc, schema)` + write `review.json` block, add:
```python
    if write_html:
        from tearsheet.builder import build_html
        html = build_html(doc, self_check=True)
        (out_dir / "review.html").write_text(html)
        print(f"[review] wrote {out_dir / 'review.html'}")

    if write_influx:
        import sys as _sys
        _sys.path.insert(0, str(_REPO / "Scripts"))
        from influx_export import export
        export(doc, algorithm_id=manifest.strategy_name, mode="backtesting",
               run_id=Path(results_dir).name, dry_run=False)
```
Ensure `doc = _result_to_dict(result)` is called once before schema-validate and reused (it already is in Task 8 — just append the html/influx block after the review.json write, before `return 0`).

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_e2e_integration.py Tests/test_review_cli.py Tests/test_review_tearsheet.py Tests/test_review_influx_export.py -v 2>&1 | tail -25
```
Expected: all PASS.

- [ ] **Step 5: Final full test run + build**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_*.py Scripts/test_strategy_review_dashboard_regression.py -v 2>&1 | tail -30
cd /home/project/hope/Lean && dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj 2>&1 | tail -3
cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2ReviewStateExportTests" 2>&1 | tail -5
```
Expected: all review tests PASS; C# build 0 errors; gold2 state-export tests PASS.

- [ ] **Step 6: Commit**

```bash
git add Scripts/review/cli.py Tests/test_review_e2e_integration.py
git commit -m "feat(review): wire --html/--influx into CLI + e2e integration tests"
```

---

## Self-Review(已执行)

**1. Spec coverage:**
- §1 架构/数据流 → Task 1(ABC)+ 7(artifacts)+ 8(CLI)+ 13(overlay)
- §2 review.json schema → Task 2(schema)+ 8(populate)+ 9(dd/tca)
- §3.1 ABC + TradeRecord + 求和目标 → Task 1
- §3.2 4 层望远镜 → Task 5(+ 求和 property test 200 cases)
- §3.3 SerializeRlState +4 字段 → Task 3
- §3.4 no-lookahead → Task 7(ts>entry realized, ts<=entry signal)
- §3.5 降级残差 → Task 6
- §4.1 HTML tearsheet → Task 10(self-check sum 不变式)
- §4.2 InfluxDB review_* → Task 11
- §4.3 Grafana dashboard → Task 12
- §5.1 manifest review: 段 → Task 4
- §5.2 CLI → Task 8
- §5.3 e2e + scheduler hook → Task 13
- §5.4 manifest_lint warnings → Task 13
- §6.1 测试 → 每个 task 内 + Task 14 e2e
- §7 文件清单 → 全覆盖
- long-only guard → Task 5 test
- short-side test → Task 6
- check-review §1(long-only 显式约束)→ Task 5 docstring + guard
- check-review §2(hook 链统一)→ Task 13(fire_optimization 末尾)+ Self-Review note
- check-review §3(§2.7 覆盖关系)→ spec 已含,plan 不重复
- check-review §4(short-side test)→ Task 6

**2. Placeholder scan:** Task 8 有 `ctx.entry_signal = {}  # alpha join留作 enrichment`。这是 v1 范围内的有意简化(alpha join 是 enrichment,不影响求和不变式;Task 14 e2e test 验证 sum 不依赖它)。非"fill in details"型 placeholder。其余无 TBD/TODO。`tpv_entry` 回填在 Task 8 step 3 明确从 state_trace entry_bar tpv 回填(非 placeholder)。

**3. Type consistency:**
- `TradeRecord` 字段跨 task 一致(symbol/entry_time/entry_price/exit_time/exit_price/quantity/side/profit_loss/fees/tpv_entry/order_ids/realized_pnl)
- `TradeContext` 字段一致(entry_bar/realized_bar/entry_signal/regime_at_entry)
- `ReviewResult` 字段一致(run_meta/layer_attribution/per_trade_narrative/drawdown_attribution/tca)
- `Gold2ReviewAdapter.LAYERS` = `["trend","vol_target","extreme_risk","realrate_cap"]` 跨 Task 4(manifest)/5(adapter)/11(influx)/12(grafana)一致
- Influx tag key `algorithm_id`(非 strategy)跨 Task 11/12 一致
- `point_to_line_protocol` / `write_lines_to_influx` / `InfluxPoint` / `escape_string_field` 签名与 `export_backtest_results_to_influx.py:62,300,304,321` 一致

**已知 v1 简化(非 placeholder):**
- `entry_signal` alpha join 留空 dict(Task 14 验证 sum 不依赖)
- `days_held` 暂填 0(per_trade schema 要求 integer;不影响核心归因)
- gold2 当前无 state_trace → adapter 跑 residual 模式(trend=PL,其余=0,sum=PL=total ✓);Task 3 接好后跑 backtest with `RL_TRACE_PATH` 产 state_trace → telescoping 解锁

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-10-trading-review-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
