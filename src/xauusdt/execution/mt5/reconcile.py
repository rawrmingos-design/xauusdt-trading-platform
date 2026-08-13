"""MT5 reconciliation (PROJECT-MT5-002, Phase 3).

Implements the contract's source-of-truth rule:

    deals > positions > orders

- ``deals_get``   — immutable history, the only trustworthy record of fills
- ``positions_get`` — what is open NOW (with our magic namespace)
- ``orders_get``  — only what is STILL PENDING

A submission is "successful" only when reconcile() confirms the expected
post-state, never from an ``order_send()`` retcode alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from xauusdt.execution.models import OrderState

log = logging.getLogger(__name__)

# OrderState values that indicate an intent may still be live at the venue.
ACTIVE_STATES = {
    OrderState.SUBMITTING,
    OrderState.SUBMITTED,
    OrderState.PARTIALLY_FILLED,
    OrderState.UNKNOWN_OUTCOME,
}


@dataclass(frozen=True)
class ReconcileReport:
    """Result of a reconcile pass over the intent store + venue truth."""

    resolved: dict[str, OrderState] = field(default_factory=dict)
    unresolved: dict[str, str] = field(default_factory=dict)  # request_id -> reason
    open_positions: list[str] = field(default_factory=list)  # adopted position tickets
    mismatches: list[str] = field(default_factory=list)


class Mt5Reconciler:
    """Reconcile persisted intents against venue truth (deals/positions/orders)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def _deals_since(self, since_ms: int) -> list[Any]:
        """Fetch deals with our comment prefix from the history window."""
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        try:
            deals = self._client.history(from_ms=since_ms, to_ms=now_ms)
        except Exception:
            log.exception("reconcile_history_failed")
            return []
        return [d for d in deals if self._is_ours(d)]

    def _is_ours(self, obj: Any) -> bool:
        """Magic-namespace membership check (position/order/deal)."""
        magic = int(getattr(obj, "magic", 0) or 0)
        base, span = self._magic_namespace()
        return base <= magic < base + span if span > 0 else False

    def _magic_namespace(self) -> tuple[int, int]:
        # Injected by the adapter via set_namespace; default conservative (none).
        return getattr(self, "_ns", (0, 0))

    def set_namespace(self, base: int, span: int) -> None:
        self._ns = (base, span)

    def _comment_of(self, obj: Any) -> str:
        return str(getattr(obj, "comment", "") or "")

    def _deals_for(self, request_id: str, deals: list[Any]) -> list[Any]:
        return [d for d in deals if self._comment_of(d) == request_id]

    def _positions_for(self, request_id: str, positions: list[Any]) -> list[Any]:
        return [p for p in positions if self._comment_of(p) == request_id]

    def reconcile(
        self,
        intents: list[dict[str, Any]],
        *,
        since_ms: int | None = None,
        positions: list[Any] | None = None,
        orders: list[Any] | None = None,
        deals: list[Any] | None = None,
    ) -> ReconcileReport:
        """Resolve active intents against venue truth.

        ``positions`` / ``orders`` / ``deals`` may be injected (tests); when
        None they are fetched from the client.
        """
        report = ReconcileReport()
        if not intents:
            return report

        if positions is None:
            positions = self._client.positions() or []
        if orders is None:
            orders = self._client.orders() or []
        if deals is None:
            since = since_ms or self._default_since_ms()
            deals = self._deals_since(since)

        for intent in intents:
            rid = intent["request_id"]
            state = OrderState(intent["state"])
            if state not in ACTIVE_STATES:
                continue

            # 1. Deals are the strongest evidence: comment match = executed.
            my_deals = self._deals_for(rid, deals)
            if my_deals:
                filled_vol = sum(float(getattr(d, "volume", 0.0) or 0.0) for d in my_deals)
                req_vol = float(intent["volume"])
                new_state = (
                    OrderState.FILLED
                    if filled_vol >= req_vol - 1e-9
                    else OrderState.PARTIALLY_FILLED
                )
                report.resolved[rid] = new_state
                log.info(
                    "mt5_reconcile_deals rid=%s state=%s vol=%.2f/%.2f",
                    rid,
                    new_state.value,
                    filled_vol,
                    req_vol,
                )
                continue

            # 2. Pending order still alive with our comment → SUBMITTED.
            my_orders = [o for o in (orders or []) if self._comment_of(o) == rid]
            if my_orders:
                report.resolved[rid] = OrderState.SUBMITTED
                log.info("mt5_reconcile_order rid=%s state=SUBMITTED", rid)
                continue

            # 3. Position with our comment → FILLED (comment carried to position).
            my_pos = self._positions_for(rid, positions or [])
            if my_pos:
                report.resolved[rid] = OrderState.FILLED
                report.open_positions.append(str(getattr(my_pos[0], "ticket", 0)))
                log.info("mt5_reconcile_position rid=%s state=FILLED", rid)
                continue

            # 4. Nothing found.
            if state is OrderState.UNKNOWN_OUTCOME:
                report.unresolved[rid] = "no_evidence_after_timeout"
            elif state is OrderState.SUBMITTED:
                # A submitted pending order that vanished without a deal may
                # have expired/cancelled server-side; report as mismatch.
                report.mismatches.append(rid)
            elif state is OrderState.SUBMITTING:
                # Never left the process (crash before send) → safe to reset.
                report.resolved[rid] = OrderState.CREATED

        return report

    @staticmethod
    def _default_since_ms() -> int:
        # Default window: last 24h. Enough for demo reconciliation.
        return int((datetime.now(UTC).timestamp() - 86400) * 1000)
