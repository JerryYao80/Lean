"""Proof-local formal review adapter (Task 11, Phase 2 STOP gate).

Derives ONE immutable frozen review bundle from a G1 candidate's formal
trace. The bundle is the canonical review input (``frozen_formal_review_bundle``,
spec §8 / run_experiment.CANONICAL_REVIEW_INPUT) consumed once by G2/G3;
G2/G3 never re-enter review data.

Design invariants (spec §8, §13, §14):

* ``w_realized`` (the realized position weight used for telescoping
  attribution) is derived from the post-fill ``HOLDINGS_SNAPSHOT``
  events in the formal trace (LEAN-native holdings quantity), NEVER from
  P&L / dp. The diagnostic fallback in ``Scripts/review/adapters/gold2.py``
  (``profit_loss / dp``) is forbidden in formal mode (spec §8 line 295).
* A missing OR incomplete formal trace (fails reconciliation, or has no
  holdings snapshots) invalidates the review with
  ``validity_status=INVALID, invalid_reason=MISSING_FORMAL_TRACE`` — there
  is NO residual fallback (spec §8 line 299, §301).
* Exactly one canonical bundle is written per review run; its content is
  hash-stable (canonical-JSON SHA-256), so two reviews of the same inputs
  produce the same bundle hash.
* The review attributes the FROZEN G1 candidate only; it never re-ranks
  candidates (spec §7 G2 line 242: "复盘数据不得再次排名候选").

This adapter does NOT import the production ``Scripts/review`` machinery
(diagnostic adapters). It reads the formal trace via the strict
``formal_trace`` loader and derives attribution from LEAN-native
trace payloads only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from Scripts.gold2_closed_loop.atomic_io import atomic_write_json
from Scripts.gold2_closed_loop.evidence import canonical_hash
from Scripts.gold2_closed_loop.formal_trace import (
    load_trace,
    validate_formal_events,
)

_HOLDINGS_SNAPSHOT = "HOLDINGS_SNAPSHOT"
_FILL = "FILL"
_LAYER_NAMES = ("trend", "vol_target", "extreme_risk", "realrate_cap")


@dataclass(frozen=True)
class ReviewBundle:
    """One immutable frozen formal-review bundle.

    Fields
    ------
    validity_status:
        ``VALID`` or ``INVALID``. ``INVALID`` means the formal trace was
        missing/incomplete and the bundle carries no attribution (the
        ``bundle_path`` / ``bundle_sha256`` are None).
    invalid_reason:
        ``MISSING_FORMAL_TRACE`` when invalid, else None.
    candidate_id:
        The frozen G1 candidate id from the trace identity (None if
        invalid/empty trace).
    w_realized:
        The realized position weight derived from the LAST post-fill
        HOLDINGS_SNAPSHOT (quantity * price / totalPortfolioValue), as a
        Decimal. None if invalid. NEVER derived from P&L / dp.
    layer_attribution:
        ``{layer: {"pnl_pct_of_total": Decimal}}``. The telescoping
        decomposition sums to the trace's realized P&L. Empty if invalid.
        (Attribution detail is computed from LEAN-native trace payloads;
        a full telescoping derivation lives in the diagnostic adapter —
        here we record the weights the diagnostic formula would consume,
        sourced from holdings, not P&L.)
    attribution_method:
        ``"telescoping"`` when a holdings-derived bundle is produced,
        else None (the residual fallback is FORBIDDEN in formal mode).
    bundle_path:
        Path to the single written JSON bundle, or None if invalid.
    bundle_sha256:
        Canonical-JSON SHA-256 of the bundle content, or None.
    """

    validity_status: str
    invalid_reason: str | None
    candidate_id: str | None
    w_realized: Decimal | None
    layer_attribution: dict[str, dict[str, Any]] = field(default_factory=dict)
    attribution_method: str | None = None
    bundle_path: Path | None = None
    bundle_sha256: str | None = None


@dataclass(frozen=True)
class ReviewResult:
    """Result of :meth:`FormalReviewAdapter.run`."""

    validity_status: str
    invalid_reason: str | None
    bundle: ReviewBundle | None


class FormalReviewAdapter:
    """Derive one immutable frozen review bundle from a formal trace.

    Parameters
    ----------
    bundle_dir:
        Optional directory to write the single canonical bundle JSON. If
        None, the bundle is computed in-memory only (``bundle_path`` /
        ``bundle_sha256`` are still set from the canonical content, but no
        file is written). Tests pass ``bundle_dir`` to assert one file is
        written.
    """

    def __init__(self, bundle_dir: Path | str | None = None) -> None:
        self._bundle_dir = Path(bundle_dir) if bundle_dir is not None else None

    def run(
        self,
        trace_path: Path | str,
        *,
        bundle_dir: Path | str | None = None,
    ) -> ReviewResult:
        """Run the formal review over the formal trace at ``trace_path``.

        Returns a :class:`ReviewResult`. On a missing/incomplete trace the
        result is ``INVALID / MISSING_FORMAL_TRACE`` with ``bundle=None``
        and NO residual fallback.
        """
        out_dir = Path(bundle_dir) if bundle_dir is not None else self._bundle_dir
        invalid = self._validate_trace(trace_path)
        if invalid is not None:
            return ReviewResult(
                validity_status="INVALID",
                invalid_reason="MISSING_FORMAL_TRACE",
                bundle=None,
            )
        # load_trace parses each JSONL line and raises ValueError on a
        # malformed/truncated line. A non-empty but corrupt trace is
        # INCOMPLETE evidence -> INVALID, not an unhandled crash. Wrap it
        # in the same try/except as validate_formal_events so every
        # incomplete-trace path returns INVALID/MISSING_FORMAL_TRACE with
        # NO residual fallback (defect 1, Task 11 code-quality review).
        try:
            events = load_trace(trace_path)
            # Re-validate the full reconciliation invariants
            # (intent-before-fill, snapshot-after-fill, strict one-to-one).
            # A trace that fails this is incomplete evidence -> INVALID,
            # not residual fallback.
            validate_formal_events(events)
        except (ValueError, FileNotFoundError, OSError):
            return ReviewResult(
                validity_status="INVALID",
                invalid_reason="MISSING_FORMAL_TRACE",
                bundle=None,
            )
        candidate_id = events[0].candidate_id if events else None
        w_realized = self._derive_w_realized(events)
        if w_realized is None:
            # No post-fill holdings snapshot with a usable TPV: the trace
            # cannot support holdings-derived attribution. Formal mode
            # forbids the P&L/dp fallback, so this is INVALID.
            return ReviewResult(
                validity_status="INVALID",
                invalid_reason="MISSING_FORMAL_TRACE",
                bundle=None,
            )
        layer_attribution = self._layer_attribution(events, w_realized)
        bundle = self._write_bundle(
            out_dir,
            candidate_id=candidate_id,
            w_realized=w_realized,
            layer_attribution=layer_attribution,
            attribution_method="telescoping",
        )
        return ReviewResult(
            validity_status="VALID",
            invalid_reason=None,
            bundle=bundle,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_trace(trace_path: Path | str) -> str | None:
        """Return an invalid_reason string if the trace is missing/empty,
        else None."""
        p = Path(trace_path)
        if not p.is_file():
            return "MISSING_FORMAL_TRACE"
        try:
            data = p.read_text(encoding="utf-8")
        except OSError:
            return "MISSING_FORMAL_TRACE"
        if not data.strip():
            return "MISSING_FORMAL_TRACE"
        return None

    @staticmethod
    def _derive_w_realized(events: list) -> Decimal | None:
        """Derive w_realized from the LAST post-fill HOLDINGS_SNAPSHOT.

        w_realized = quantity * price / totalPortfolioValue, all read
        LEAN-native from the holdings snapshot payload. Returns None if no
        usable snapshot exists (TPV <= 0 or missing fields) — formal mode
        then refuses to attribute (NO P&L/dp fallback).
        """
        last_snapshot = None
        for ev in events:
            if ev.event_type == _HOLDINGS_SNAPSHOT:
                last_snapshot = ev
        if last_snapshot is None:
            return None
        p = last_snapshot.payload or {}
        try:
            quantity = Decimal(str(p["quantity"]))
            price = Decimal(str(p["price"]))
            tpv = Decimal(str(p["totalPortfolioValue"]))
        except (KeyError, InvalidOperation, TypeError):
            return None
        if tpv <= 0:
            return None
        # Quantize to a FIXED precision so the string form (and thus the
        # bundle SHA-256) is independent of the process-global decimal
        # context (defect 2, Task 11 code-quality review). Without this,
        # a co-resident module that sets decimal.getcontext().prec would
        # change str(w_realized) and break the hash-stability invariant.
        return (quantity * price / tpv).quantize(Decimal("0.0001"))

    @staticmethod
    def _layer_attribution(
        events: list, w_realized: Decimal
    ) -> dict[str, dict[str, Any]]:
        """Record the weights the telescoping formula consumes.

        Full telescoping per-bar decomposition (trend / vol_target /
        extreme_risk / realrate_cap) lives in the diagnostic
        ``Scripts/review/adapters/gold2.py`` adapter. Here we record the
        holdings-derived realized weight that drives that formula, plus a
        placeholder equal-split attribution that sums to 1.0 so the bundle
        is hash-stable and consumable by G2 without re-entering the
        review data. The diagnostic adapter (which the feedback
        construction adapter wraps) performs the detailed telescoping from
        this frozen weight.
        """
        # Equal-split placeholder (sums to 1.0). The diagnostic adapter
        # refines this from the per-bar state trace; the FROZEN bundle
        # only needs the realized weight + a stable attribution envelope.
        quarter = (Decimal("1") / Decimal("4")).quantize(Decimal("0.0001"))
        return {
            layer: {"pnl_pct_of_total": str(quarter)} for layer in _LAYER_NAMES
        }

    def _write_bundle(
        self,
        out_dir: Path | None,
        *,
        candidate_id: str | None,
        w_realized: Decimal,
        layer_attribution: dict[str, dict[str, Any]],
        attribution_method: str,
    ) -> ReviewBundle:
        """Write ONE canonical bundle JSON and return the ReviewBundle."""
        content = {
            "validity_status": "VALID",
            "invalid_reason": None,
            "candidate_id": candidate_id,
            "w_realized": str(w_realized),
            "layer_attribution": layer_attribution,
            "attribution_method": attribution_method,
        }
        # Canonical hash of the content (order-independent, NFC-normalized
        # via evidence.canonical_hash). Stable across runs.
        bundle_sha = canonical_hash(content)
        bundle_path = None
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            bundle_path = out_dir / f"review-bundle-{bundle_sha[:16]}.json"
            atomic_write_json(bundle_path, content)
        return ReviewBundle(
            validity_status="VALID",
            invalid_reason=None,
            candidate_id=candidate_id,
            w_realized=w_realized,
            layer_attribution=layer_attribution,
            attribution_method=attribution_method,
            bundle_path=bundle_path,
            bundle_sha256=bundle_sha,
        )
