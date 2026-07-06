"""OptionVolArb5Layer 字段审计 (spec §2.3, self-update23.md).

核实 SerializeRlState 输出的 drawdown/pnl_1d/days_held 计算逻辑是否正确.
"""
from dataclasses import dataclass


@dataclass
class FieldAuditReport:
    consistent: bool
    trace_max_drawdown: float
    stats_drawdown_fraction: float
    reason: str


def _parse_percent(val) -> float:
    if val is None: return None
    s = str(val).replace(",", "").strip()
    had_pct = "%" in s
    s = s.replace("%", "")
    try:
        f = float(s)
        return f / 100.0 if had_pct else (f / 100.0 if f > 1 else f)
    except ValueError:
        return None


def audit_drawdown_consistency(trace: list, stats: dict, tolerance: float = 0.01) -> FieldAuditReport:
    stats_dd = _parse_percent(stats.get("Drawdown"))
    if stats_dd is None:
        return FieldAuditReport(consistent=False, trace_max_drawdown=0.0,
                                stats_drawdown_fraction=0.0, reason="missing Drawdown in stats")
    trace_dd = max((float(s.get("drawdown", 0)) for s in trace), default=0.0)
    diff = abs(trace_dd - stats_dd)
    return FieldAuditReport(
        consistent=diff < tolerance,
        trace_max_drawdown=trace_dd,
        stats_drawdown_fraction=stats_dd,
        reason=f"diff={diff:.4f} tolerance={tolerance}",
    )


def audit_pnl_1d_sign(trace: list) -> dict:
    positive_on_up = 0; total = 0
    for s in trace:
        pnl = float(s.get("pnl_1d", 0))
        if pnl != 0:
            total += 1
            if pnl > 0: positive_on_up += 1
    return {"non_zero_count": total, "positive_ratio": positive_on_up / max(total, 1)}


def audit_days_held_boundary(trace: list) -> dict:
    negative = sum(1 for s in trace for p in s.get("positions", [])
                   if p.get("days_held", 0) < 0)
    return {"negative_days_held_count": negative, "ok": negative == 0}
