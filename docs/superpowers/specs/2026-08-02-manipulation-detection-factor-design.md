# Manipulation Detection Factor Design

> **Date**: 2026-08-02
> **Scope**: CSI300 constituent stocks, daily EOD data
> **Objective**: Detect institutional manipulation signals for reverse trading strategies
>
> **Related docs**:
> - `docs/ashare-manipulation.md` — A-share manipulation tactics research
> - `docs/volatility-chip-manipulation-detection-research.md` — Volatility & chip detection
> - `docs/research/lean_ashare_capabilities.md` — LEAN A-share capability survey
> - `docs/research/ashare_market_manipulation_research.md` — Manipulation research findings

---

## 1. Overview

Build 4 new manipulation detection factors following the existing Phase 5 builder pattern. Each factor is a standalone Python builder producing parquet + InfluxDB output, registered in `factor_worker.py`.

## 2. Factors

### 2.1 `turnover_anomaly` — Turnover Rate Anomaly

**Formula**: `z = (turnover_t - mean(turnover, 20d)) / std(turnover, 20d)`

**Interpretation**:
- `z > 2`: Abnormally high turnover (wash trading or distribution)
- `z < -2`: Abnormally low turnover (institutional lock-up)

**Data**: `daily_basic.turnover_rate`

**Builder**: `factor_builders/turnover_anomaly_builder.py`

---

### 2.2 `amplitude_anomaly` — Intraday Amplitude Anomaly

**Formula**: `z = (amplitude_t - mean(amplitude, 20d)) / std(amplitude, 20d)`

Where `amplitude = (high - low) / pre_close`

**Interpretation**:
- `z > 2`: Abnormally large amplitude (manipulation or high volatility)
- `z < -2`: Compressed amplitude (calm before storm)

**Data**: `daily.high`, `daily.low`, `daily.pre_close`

**Builder**: `factor_builders/amplitude_anomaly_builder.py`

---

### 2.3 `limit_behavior` — Limit-Up/Down Behavior

**Formula**: `limit_score = limit_up_count_5d / 5 + limit_down_count_5d / 5`

**Interpretation**:
- `limit_score > 0.6`: Frequent limit-up (strong manipulation or momentum)
- `limit_score > 0` with down: Mixed behavior (unstable)

**Data**: `limit_list_d`

**Builder**: `factor_builders/limit_behavior_builder.py`

---

### 2.4 `intraday_reversal` — Intraday Reversal Strength

**Formula**: `reversal = (close - low) / (high - low)`

**Interpretation**:
- `reversal > 0.8`: Strong reversal from low (accumulation)
- `reversal < 0.2`: Reversal from high (distribution)

**Data**: `daily.high`, `daily.low`, `daily.close`

**Builder**: `factor_builders/intraday_reversal_builder.py`

---

## 3. Composite Score (Future)

`manipulation_risk_score = weighted_sum(turnover_z, amplitude_z, limit_score, reversal)`

Weights optimized via backtest (not in Phase 1).

---

## 4. Registration

Add to `factor_worker.py` BUILDERS list:

```python
FactorBuilder(
    factor_id="turnover_anomaly",
    build_callable=_BLD_TURNOVER, latest_date_resolver=_RES_TURNOVER,
    depends_on=("daily_basic",), max_backfill_days=60,
),
FactorBuilder(
    factor_id="amplitude_anomaly",
    build_callable=_BLD_AMPLITUDE, latest_date_resolver=_RES_AMPLITUDE,
    depends_on=("daily",), max_backfill_days=60,
),
FactorBuilder(
    factor_id="limit_behavior",
    build_callable=_BLD_LIMIT, latest_date_resolver=_RES_LIMIT,
    depends_on=("limit_list_d",), max_backfill_days=60,
),
FactorBuilder(
    factor_id="intraday_reversal",
    build_callable=_BLD_REVERSAL, latest_date_resolver=_RES_REVERSAL,
    depends_on=("daily",), max_backfill_days=60,
),
```

---

## 5. Testing

- Unit test: Each builder produces correct values for known input
- Integration test: `factor_worker` registers and runs successfully
- Backtest: Signal correlation with future returns
