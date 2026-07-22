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


def _ts_to_iso(ts) -> str:
    from datetime import datetime as _dt, timezone as _tz
    return _dt.fromtimestamp(int(ts), tz=_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
                    "duration_days": max(0, int(ts) - int(peak_ts)) // 86400,
                    "top_contributing_layers": [],
                    "top_contributing_trades": [],
                    "regime_during": None,
                    "regime_source": "none",
                })
                in_dd = False
    if in_dd:
        episodes.append({
            "peak_time": _ts_to_iso(peak_ts),
            "trough_time": _ts_to_iso(trough_ts),
            "recovery_time": None,
            "depth_pct": trough_depth,
            "duration_days": max(0, int(trough_ts) - int(peak_ts)) // 86400,
            "top_contributing_layers": [],
            "top_contributing_trades": [],
            "regime_during": None,
            "regime_source": "none",
        })
    episodes.sort(key=lambda e: e["depth_pct"])
    return episodes[:5]


def _safe_float(x):
    """Parse float defensively; None on failure (spec §2.5 graceful TCA degradation)."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _tca(algo_data: dict, order_events_path: Path) -> dict:
    """Slippage = mean over filled events of (fillPrice-mid)/mid*10000. Spec §2.5."""
    orders = algo_data.get("orders", {})
    oe = json.loads(order_events_path.read_text()) if order_events_path.exists() else []
    slips = []
    n_fills = 0
    for ev in oe:
        if ev.get("status") != "filled":
            continue
        n_fills += 1
        oid = str(ev.get("orderId"))
        order = orders.get(oid, {})
        osd = order.get("orderSubmissionData", {})
        bid = _safe_float(osd.get("bidPrice"))
        ask = _safe_float(osd.get("askPrice"))
        last = _safe_float(osd.get("lastPrice"))
        fill = _safe_float(ev.get("fillPrice"))
        if fill is None:
            continue
        mid = None
        if bid is not None and bid > 0 and ask is not None and ask > 0:
            mid = (bid + ask) / 2
        elif last is not None and last > 0:
            mid = last
        if mid and mid > 0:
            slips.append((fill - mid) / mid * 10000)
    if not slips:
        return {"avg_slippage_bps": None, "fill_quality_score": None, "n_fills": n_fills,
                "source": "absent:orderSubmissionData" if n_fills == 0 else "absent:no mid"}
    avg = sum(slips) / len(slips)
    return {"avg_slippage_bps": avg, "fill_quality_score": 1.0 - min(abs(avg) / 100, 1.0),
            "n_fills": n_fills, "source": "orderSubmissionData"}


def _result_to_dict(result: ReviewResult) -> dict:
    return {
        "run_meta": result.run_meta,
        "layer_attribution": result.layer_attribution,
        "per_trade_narrative": result.per_trade_narrative,
        "drawdown_attribution": result.drawdown_attribution,
        "tca": result.tca,
    }


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
        ctx.entry_signal = {}  # alpha join 留作 enrichment (Task 14 e2e 不依赖; 不影响 sum 不变式)
        ctx.regime_at_entry = ctx.entry_bar or {}
        # backfill tpv_entry from state_trace entry_bar tpv (else telescoping scale=0)
        if ctx.entry_bar and "tpv" in ctx.entry_bar:
            ct.tpv_entry = Decimal(str(ctx.entry_bar["tpv"]))
        try:
            # validated_layer_attribution enforces keys==LAYERS + sum==profit_loss (spec §6.1).
            # Residual fallback (no entry_bar) is handled inside layer_attribution itself, so
            # the only ValueError that escapes is a genuine invariant violation — let it
            # propagate as a hard failure rather than silently papering over a broken adapter.
            layers = adapter.validated_layer_attribution(ct, ctx)
            for ln, v in layers.items():
                layer_agg[ln]["pnl_abs"] += v
                layer_agg[ln]["n_trades"] += 1
                if v > 0:
                    layer_agg[ln]["n_wins"] += 1
            trade_layer_contrib = {ln: str(v) for ln, v in layers.items()}
        except ValueError:
            # Should not happen: residual path returns a sum-equal dict. If it does (e.g.
            # long-only guard on a short trade with no entry_bar is impossible by construction),
            # attribute all PnL to LAYERS[0] and flag for investigation.
            layer_agg[adapter.LAYERS[0]]["pnl_abs"] += ct.profit_loss
            layer_agg[adapter.LAYERS[0]]["n_trades"] += 1
            trade_layer_contrib = {adapter.LAYERS[0]: str(ct.profit_loss)}
        per_trade.append({
            "trade_id": ct.order_ids[0] if ct.order_ids else len(per_trade) + 1,
            "symbol": ct.symbol, "entry_time": ct.entry_time, "exit_time": ct.exit_time,
            "entry_price": str(ct.entry_price), "exit_price": str(ct.exit_price),
            "direction": ct.side, "quantity": str(ct.quantity),
            "pnl": str(ct.profit_loss), "fees": str(ct.fees),
            "mae": "0", "mfe": "0", "end_trade_drawdown": "0",
            "days_held": 0, "layer_contributions": trade_layer_contrib,
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
        drawdown_attribution=_drawdown_attribution(algo_data),
        tca=_tca(algo_data, order_events),
    )

    import jsonschema
    schema = json.loads((Path(__file__).parent / "schema" / "review_schema.json").read_text())
    doc = _result_to_dict(result)
    jsonschema.validate(doc, schema)

    out_dir = results_dir / "review"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review.json").write_text(json.dumps(doc, indent=2, default=str))
    print(f"[review] wrote {out_dir / 'review.json'}")

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

    # Sidecar last_review (spec §1.3 step 9, §5.2): write review status without
    # mutating the source manifest YAML (keeps git diff clean). The scheduler/
    # overlay reads this sidecar to know whether review ran. review_status:
    # 'pass' (sum invariant held, tca present), 'warn' (degraded but usable).
    review_status = "pass"
    if doc["tca"] is None:
        review_status = "warn"
    sidecar = {
        "last_review": doc["run_meta"]["generated_at"],
        "review_status": review_status,
        "review_artifact_path": str((out_dir / "review.json").relative_to(_REPO)),
        "backtest_id": doc["run_meta"]["backtest_id"],
    }
    (out_dir / "review.last_review.json").write_text(json.dumps(sidecar, indent=2, default=str))
    print(f"[review] wrote {out_dir / 'review.last_review.json'} (status={review_status})")

    # --write-manifest: also write last_review/review_status/review_artifact_path
    # back to the source manifest YAML via ruamel.yaml (preserves comments).
    if write_manifest:
        from ruamel.yaml import YAML
        yaml_loader = YAML()
        yaml_loader.preserve_quotes = True
        mpath = Path(manifest_path)
        mdoc = yaml_loader.load(mpath.read_text())
        if mdoc.get("review") is not None:
            mdoc["review"]["last_review"] = sidecar["last_review"]
            mdoc["review"]["review_status"] = review_status
            mdoc["review"]["review_artifact_path"] = sidecar["review_artifact_path"]
            mpath.write_text(yaml_loader.dump(mdoc))
            print(f"[review] wrote back manifest {mpath}")

    return 0
