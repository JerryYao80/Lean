"""Gold2FeedbackAdapter — trigger + min_trades gate + shaping_overrides. Spec §3.2, §3.3.

per-bar 下采样 (§3.3) 在 Task 5 实现;本 task 做触发器 + shaping_overrides + observation_fields 透传。
"""
from .base import StrategyFeedbackAdapter, FeedbackAction


class Gold2FeedbackAdapter(StrategyFeedbackAdapter):
    LAYERS = ["trend", "vol_target", "extreme_risk", "realrate_cap"]

    def feedback_signal(self, manifest, review_doc, state_trace):
        fb = (manifest.raw if manifest else {}).get("feedback", {})
        thresholds = fb.get("trigger_thresholds", {})
        term_map = fb.get("shaping_term_map", {})
        obs_fields = fb.get("observation_fields", [])

        trigger, reasons = self._check_triggers(review_doc, thresholds)
        shaping = self._compute_shaping_overrides(review_doc, thresholds, term_map)
        per_bar = self._downsample_per_bar(state_trace) if state_trace else []

        return FeedbackAction(
            trigger=trigger,
            trigger_reason="; ".join(reasons),
            shaping_overrides=shaping,
            observation_fields=obs_fields,
            attribution_method=review_doc.get("run_meta", {}).get("attribution_method", "residual"),
            per_bar_layer_contrib=per_bar,
        )

    def _check_triggers(self, review_doc, thresholds):
        """Spec §3.2: review_status=fail (hard) + layer_gap (gated by min_trades)."""
        trigger = False
        reasons = []
        if review_doc.get("review_status") == "fail" and thresholds.get("review_status_fail", True):
            trigger = True
            reasons.append("review_status=fail")
        n_trades = len(review_doc.get("per_trade_narrative", []))
        min_trades = thresholds.get("min_narrative_trades", 20)
        if n_trades < min_trades:
            reasons.append(f"warn: n_trades={n_trades}<{min_trades}, layer_gap skipped (small-sample)")
        else:
            gap_thresh = thresholds.get("max_layer_attribution_gap", 0.15)
            for layer, agg in review_doc.get("layer_attribution", {}).items():
                pct = abs(agg.get("pnl_pct_of_total", 0))
                if pct > gap_thresh:
                    trigger = True
                    reasons.append(f"layer_gap {layer}={agg.get('pnl_pct_of_total')}>{gap_thresh}")
        return trigger, reasons

    def _compute_shaping_overrides(self, review_doc, thresholds, term_map):
        """Spec §3.3: weight = clamp(gap/threshold, 0.5, 3.0) for layers exceeding gap."""
        shaping = {}
        gap_thresh = thresholds.get("max_layer_attribution_gap", 0.15)
        for layer, term in term_map.items():
            agg = review_doc.get("layer_attribution", {}).get(layer, {})
            gap = abs(agg.get("pnl_pct_of_total", 0))
            if gap > gap_thresh:
                weight = gap / gap_thresh
                shaping[term] = max(0.5, min(weight, 3.0))
        return shaping

    def _downsample_per_bar(self, state_trace):
        """Spec §3.3: per-bar telescoping. Implemented in Task 5."""
        return []
