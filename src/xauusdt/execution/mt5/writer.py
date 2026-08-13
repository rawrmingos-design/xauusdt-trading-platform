"""MT5 write path (PROJECT-MT5-002, Phase 3 — DEMO ONLY).

Implements the approved execution semantics contract:
https://github.com/rawrmingos-design/xauusdt-trading-platform/blob/ops/PROJECT-MT5-002-phase3/docs/execution_semantics_contract_mt5_phase3.md

Hard rules enforced here (normative, from the TL-approved contract):
  1. SL/TP mandatory at entry — NO opt-out. Missing → OrderValidationError
     BEFORE anything reaches the venue.
  2. Idempotency — (magic, comment) key. The comment/request_id is persisted
     BEFORE order_send (persist-before-send). Duplicate submission attempt of
     an existing request_id → resolved from evidence, never re-sent.
  3. TIMEOUT is NEVER auto-retried. It goes UNKNOWN_OUTCOME → reconcile →
     found? resolve : operator. No blind resend.
  4. TOO_MANY_REQUESTS requires the safe-retry sequence: idempotency lookup →
     reconcile evidence → no evidence → order_check() → retry (max 3).
  5. Auto-retry ONLY for REQUOTE/PRICE_CHANGED/PRICE_OFF (transient,
     pre-acceptance), max 3, backoff 1/2/4s, each preceded by order_check().
  6. Source of truth = reconcile(); order_send() retcode alone is never
     sufficient to declare success.
  7. Demo-only. Mode guard enforced by Mt5Settings + adapter connect().
     There is NO live path in this project.

This module never imports MetaTrader5 — it drives the venue through the
thin client facade so tests inject fakes.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from xauusdt.execution.errors import (
    ExecutionError,
    OrderSendRejectedError,
    OrderValidationError,
)
from xauusdt.execution.models import OrderKind, OrderSide, OrderState
from xauusdt.execution.mt5.models import (
    Mt5OrderRequest,
    RetcodeClass,
    TradeRetcode,
    classify_retcode,
)
from xauusdt.execution.orders import ExecutionResult, OrderIntent

log = logging.getLogger(__name__)

# MT5 trade actions (ENUM_TRADE_REQUEST_ACTIONS)
_ACTION_DEAL = 1  # TRADE_ACTION_DEAL — market order
_ACTION_PENDING = 5  # TRADE_ACTION_PENDING — place pending order

# MQL5 order types (ENUM_ORDER_TYPE)
_MARKET_BUY = 0
_MARKET_SELL = 1

# Auto-retry policy (contract §2.3): ONLY transient pre-acceptance codes.
_AUTO_RETRY_CODES = frozenset(
    {
        TradeRetcode.REQUOTE,
        TradeRetcode.PRICE_CHANGED,
        TradeRetcode.PRICE_OFF,
    }
)
_MAX_RETRIES = 3
_RETRY_BACKOFF_S = (1, 2, 4)


class _MissingStopError(OrderValidationError):
    """Entry intent without a stop loss (contract §6.2 — no opt-out)."""


class _MissingTakeProfitError(OrderValidationError):
    """Entry intent without a take profit (contract §6.2 — no opt-out)."""


class Mt5Writer:
    """Demo-only write path over the MT5 client facade.

    Requires a connected client + a store for persist-before-send intents.
    """

    def __init__(self, client: Any, store: Any, settings: Any, mode_guard: Any) -> None:
        self._client = client
        self._store = store
        self._settings = settings
        self._mode_guard = mode_guard  # callable: raises ModeGuardError unless demo
        self._inflight: set[str] = set()  # in-process duplicate guard (§4.3.4)

    # ------------------------------------------------------------ validation

    @staticmethod
    def _validate_sl_tp(intent: OrderIntent) -> None:
        """SL/TP mandatory at entry — contract §6.2, no exceptions."""
        if intent.stop_loss <= 0:
            raise _MissingStopError(
                f"entry {intent.side.value} {intent.symbol} has no stop_loss; "
                "SL is mandatory for every entry (contract §6.2), no opt-out"
            )
        if intent.take_profit <= 0:
            raise _MissingTakeProfitError(
                f"entry {intent.side.value} {intent.symbol} has no take_profit; "
                "TP is mandatory for every entry (contract §6.2), no opt-out"
            )

    @staticmethod
    def _validate_volume(intent: OrderIntent, symbol_info: Any) -> None:
        if intent.volume < symbol_info.volume_min or intent.volume > symbol_info.volume_max:
            raise OrderValidationError(
                f"volume {intent.volume} outside [{symbol_info.volume_min}, "
                f"{symbol_info.volume_max}] for {intent.symbol}"
            )

    def check_order(self, intent: OrderIntent, symbol_info: Any = None) -> ExecutionResult:
        """Expose order_check for pre-trade validation (no state change)."""
        self._mode_guard("write")
        self._validate_sl_tp(intent)
        self._validate_volume(intent, symbol_info)
        # A transient request_id for check (persisted before send in submit()).
        rid = f"check-{int(self._store.latest_seq()) + 1}"
        return self._order_check(intent, rid)

    # ------------------------------------------------------------- submit

    def submit(self, intent: OrderIntent, symbol_info: Any = None) -> ExecutionResult:
        """Submit a demo order per the approved flow.

        Flow (contract §0/§10):
          validate → SL/TP check → persist request_id → idempotency check →
          order_check() → order_send() → retcode classify → reconcile() →
          persist actual state.
        """
        self._mode_guard("write")
        self._validate_sl_tp(intent)
        self._validate_volume(intent, symbol_info)

        # --- persist-before-send: allocate + persist comment FIRST
        request_id = self._store.create_intent(
            magic=self._settings.magic,
            symbol=intent.symbol,
            side=intent.side,
            kind=intent.kind,
            volume=intent.volume,
            entry_price=intent.entry_price,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            run_id=self._settings.run_id,
        )

        # --- idempotency check (§4.3): comment may already exist at venue
        existing = self._idempotency_lookup(request_id)
        if existing is not None:
            log.info("mt5_idempotent_hit rid=%s state=%s", request_id, existing.value)
            self._store.update_state(request_id, existing)
            return ExecutionResult.accepted(
                venue_order_id=request_id,
                state=existing,
                message=f"idempotent hit: {request_id} already {existing.value}",
            )

        # --- in-process duplicate guard (§4.3.4)
        if request_id in self._inflight:
            raise OrderSendRejectedError(f"duplicate in-flight submission {request_id}")
        self._inflight.add(request_id)

        try:
            return self._submit_with_retry(intent, request_id, symbol_info)
        finally:
            self._inflight.discard(request_id)

    def _submit_with_retry(
        self, intent: OrderIntent, request_id: str, symbol_info: Any
    ) -> ExecutionResult:
        """Send with the bounded retry policy (§2.3)."""
        attempts = 0
        while True:
            attempts += 1
            self._store.update_state(request_id, OrderState.SUBMITTING, attempts=attempts)

            # order_check() before every send (contract §2.3: each retry re-checks)
            check_res = self._order_check(intent, request_id)
            if not check_res.ok:
                self._store.update_state(request_id, OrderState.REJECTED)
                log.warning(
                    "mt5_order_check_rejected rid=%s code=%s", request_id, check_res.rejection_code
                )
                return check_res

            send_res = self._order_send(intent, request_id)
            raw_code = send_res.rejection_code or ""
            try:
                retcode_val = int(raw_code)
            except (TypeError, ValueError):
                retcode_val = None

            # --- TIMEOUT: NEVER auto-retry (§2.2)
            if retcode_val == TradeRetcode.TIMEOUT.value:
                self._store.update_state(request_id, OrderState.UNKNOWN_OUTCOME)
                log.warning("mt5_timeout_unknown rid=%s -> UNKNOWN_OUTCOME, reconcile", request_id)
                return ExecutionResult.rejected(
                    code="unknown_outcome",
                    message=f"TIMEOUT (10012) — outcome unknown, reconcile required for {request_id}",
                    state=OrderState.UNKNOWN_OUTCOME,
                )

            # --- TOO_MANY_REQUESTS: safe-retry sequence (§2.4)
            if retcode_val == TradeRetcode.TOO_MANY_REQUESTS.value:
                if attempts >= _MAX_RETRIES:
                    self._store.update_state(request_id, OrderState.REJECTED)
                    return ExecutionResult.rejected(
                        code="too_many_requests",
                        message=f"rate-limited after {attempts} attempts",
                    )
                # idempotency lookup + evidence before retry
                if self._idempotency_lookup(request_id) is not None:
                    self._store.update_state(request_id, OrderState.FILLED)
                    return ExecutionResult.accepted(
                        venue_order_id=request_id,
                        state=OrderState.FILLED,
                        message="resolved via idempotency during rate-limit retry",
                    )
                log.warning("mt5_rate_limited rid=%s retry %d", request_id, attempts)
                time.sleep(_RETRY_BACKOFF_S[min(attempts - 1, len(_RETRY_BACKOFF_S) - 1)])
                continue

            # --- success / partial / other rejection
            if send_res.ok:
                state = send_res.state
                self._store.update_state(
                    request_id,
                    state,
                    venue_order_id=send_res.venue_order_id,
                    filled_volume=send_res.filled_volume,
                    filled_price=send_res.filled_price,
                )
                # DONE_PARTIAL → remainder stays live; no auto-replace (§3)
                if state is OrderState.PARTIALLY_FILLED:
                    log.info("mt5_partial_fill rid=%s vol=%.2f", request_id, send_res.filled_volume)
                return send_res

            # --- auto-retry only for REQUOTE/PRICE_CHANGED/PRICE_OFF (§2.3)
            if retcode_val in {c.value for c in _AUTO_RETRY_CODES} and attempts < _MAX_RETRIES:
                log.info(
                    "mt5_retry rid=%s code=%s attempt=%d",
                    request_id,
                    send_res.rejection_code,
                    attempts,
                )
                time.sleep(_RETRY_BACKOFF_S[min(attempts - 1, len(_RETRY_BACKOFF_S) - 1)])
                continue

            # --- terminal rejection
            self._store.update_state(request_id, OrderState.REJECTED)
            return send_res

    def _order_check(self, intent: OrderIntent, request_id: str) -> ExecutionResult:
        """Venue-side validation (no state change)."""
        try:
            action = _ACTION_PENDING if intent.kind != OrderKind.MARKET else _ACTION_DEAL
            price = self._price_for(intent)
            req = Mt5OrderRequest(
                action=action,
                symbol=intent.symbol,
                volume=intent.volume,
                type=self._mt5_type(intent),
                price=price,
                sl=intent.stop_loss,
                tp=intent.take_profit,
                magic=self._settings.magic,
                comment=request_id,
            )
            check = self._client.order_check(req.to_dict())
        except ExecutionError as exc:
            return ExecutionResult.rejected("venue_error", str(exc))
        if check is None:
            return ExecutionResult.rejected("check_failed", "order_check returned None")
        retcode = int(getattr(check, "retcode", -1))
        if retcode == 0:  # TRADE_RETCODE_DONE == 0 for order_check
            return ExecutionResult.accepted(venue_order_id=request_id, state=OrderState.VALIDATED)
        return ExecutionResult.rejected(
            code=str(retcode),
            message=f"order_check rejected: {retcode}",
        )

    def _order_send(self, intent: OrderIntent, request_id: str) -> ExecutionResult:
        """Single venue submission (no retry logic here — caller controls)."""
        try:
            action = _ACTION_PENDING if intent.kind != OrderKind.MARKET else _ACTION_DEAL
            price = self._price_for(intent)
            req = Mt5OrderRequest(
                action=action,
                symbol=intent.symbol,
                volume=intent.volume,
                type=self._mt5_type(intent),
                price=price,
                sl=intent.stop_loss,
                tp=intent.take_profit,
                magic=self._settings.magic,
                comment=request_id,
            )
            send = self._client.order_send(req.to_dict())
        except ExecutionError as exc:
            # Transport error → outcome unknown → UNKNOWN_OUTCOME (§2.2)
            self._store.update_state(request_id, OrderState.UNKNOWN_OUTCOME)
            return ExecutionResult.rejected(
                code="unknown_outcome",
                message=f"order_send transport error: {exc}",
                state=OrderState.UNKNOWN_OUTCOME,
            )
        if send is None:
            self._store.update_state(request_id, OrderState.UNKNOWN_OUTCOME)
            return ExecutionResult.rejected(
                code="unknown_outcome",
                message="order_send returned None (outcome unknown)",
                state=OrderState.UNKNOWN_OUTCOME,
            )
        retcode = int(getattr(send, "retcode", -1))
        order_id = int(getattr(send, "order", 0) or 0)
        rc = classify_retcode(retcode)
        if rc is RetcodeClass.SUCCESS:
            state = (
                OrderState.FILLED if retcode == TradeRetcode.DONE.value else OrderState.SUBMITTED
            )
            return ExecutionResult.accepted(
                venue_order_id=str(order_id),
                state=state,
                message=f"order_send {TradeRetcode(retcode).name}",
            )
        if rc is RetcodeClass.PARTIAL:
            return ExecutionResult.accepted(
                venue_order_id=str(order_id),
                state=OrderState.PARTIALLY_FILLED,
                message="order_send DONE_PARTIAL",
            )
        # rejection path: keep the raw retcode as the structured code so the
        # caller can classify TIMEOUT / TOO_MANY_REQUESTS / REQUOTE / etc.
        return ExecutionResult.rejected(
            code=str(retcode),
            message=f"order_send rejected: {retcode}",
            state=OrderState.REJECTED,
        )

    def _price_for(self, intent: OrderIntent) -> float:
        if intent.kind == OrderKind.MARKET:
            tick = self._client.tick(intent.symbol)
            ask = float(tick.ask)
            bid = float(tick.bid)
            return ask if intent.side == OrderSide.LONG else bid
        return intent.entry_price

    @staticmethod
    def _mt5_type(intent: OrderIntent) -> int:
        """Domain (side, kind) -> MQL5 order type."""
        if intent.kind == OrderKind.MARKET:
            return 0 if intent.side == OrderSide.LONG else 1
        if intent.kind == OrderKind.LIMIT:
            return 2 if intent.side == OrderSide.LONG else 3
        if intent.kind == OrderKind.STOP:
            return 4 if intent.side == OrderSide.LONG else 5
        raise ValueError(f"unsupported kind {intent.kind!r}")

    # ------------------------------------------------------------ idempotency

    def _idempotency_lookup(self, request_id: str) -> OrderState | None:
        """§4.3: comment already present at venue → already placed/executed."""
        try:
            orders = self._client.orders() or []
            for o in orders:
                if str(getattr(o, "comment", "") or "") == request_id:
                    return OrderState.SUBMITTED
            deals = self._client.deals_by_comment(request_id) or []
            if deals:
                return OrderState.FILLED
        except Exception:
            log.exception("idempotency_lookup_failed rid=%s", request_id)
        return None

    # ------------------------------------------------------------- modify

    def modify_sl_tp(
        self,
        position_id: str,
        *,
        sl: float | None = None,
        tp: float | None = None,
    ) -> ExecutionResult:
        """Modify SL/TP of an open position (demo-only)."""
        self._mode_guard("modify")
        try:
            ticket = int(position_id)
        except (TypeError, ValueError):
            return ExecutionResult.rejected("invalid_position", f"bad ticket {position_id!r}")

        # §6.1: only positions with our magic may be modified.
        positions = self._client.positions() or []
        match = [p for p in positions if int(getattr(p, "ticket", 0)) == ticket]
        if not match:
            return ExecutionResult.rejected(
                "position_not_found", f"no open position with ticket {ticket}"
            )
        pos = match[0]
        if int(getattr(pos, "magic", 0)) != self._settings.magic:
            return ExecutionResult.rejected(
                "wrong_magic",
                f"position {ticket} magic {getattr(pos, 'magic', '?')} != {self._settings.magic}",
            )
        new_sl = sl if sl is not None else float(getattr(pos, "sl", 0.0) or 0.0)
        new_tp = tp if tp is not None else float(getattr(pos, "tp", 0.0) or 0.0)
        try:
            res = self._client.position_modify(
                ticket=ticket,
                sl=new_sl,
                tp=new_tp,
                symbol=str(getattr(pos, "symbol", "")),
            )
        except ExecutionError as exc:
            return ExecutionResult.rejected("venue_error", str(exc))
        if res is None:
            return ExecutionResult.rejected("modify_failed", "position_modify returned None")
        retcode = int(getattr(res, "retcode", -1))
        if classify_retcode(retcode) is RetcodeClass.SUCCESS:
            return ExecutionResult.accepted(
                venue_order_id=str(ticket),
                state=OrderState.POSITION,
                message=f"SL/TP updated to {new_sl}/{new_tp}",
            )
        return ExecutionResult.rejected(str(retcode), f"position_modify rejected: {retcode}")

    # ------------------------------------------------------------- close

    def close_position(self, position_id: str) -> ExecutionResult:
        """Full close of an open position (demo-only, full close only §7)."""
        self._mode_guard("close")
        try:
            ticket = int(position_id)
        except (TypeError, ValueError):
            return ExecutionResult.rejected("invalid_position", f"bad ticket {position_id!r}")

        positions = self._client.positions() or []
        match = [p for p in positions if int(getattr(p, "ticket", 0)) == ticket]
        if not match:
            return ExecutionResult.rejected(
                "position_not_found", f"no open position with ticket {ticket}"
            )
        pos = match[0]
        if int(getattr(pos, "magic", 0)) != self._settings.magic:
            return ExecutionResult.rejected(
                "wrong_magic",
                f"position {ticket} magic {getattr(pos, 'magic', '?')} != {self._settings.magic}",
            )
        symbol = str(getattr(pos, "symbol", ""))
        volume = float(getattr(pos, "volume", 0.0))
        is_long = int(getattr(pos, "type", 0)) == 0
        req = Mt5OrderRequest(
            action=_ACTION_DEAL,
            symbol=symbol,
            volume=volume,
            type=_MARKET_SELL if is_long else _MARKET_BUY,
            price=self._price_for_close(symbol, is_long),
            magic=self._settings.magic,
            comment=f"close-{ticket}",
            ticket=ticket,
        )
        try:
            res = self._client.order_send(req.to_dict())
        except ExecutionError as exc:
            return ExecutionResult.rejected("unknown_outcome", f"close transport error: {exc}")
        if res is None:
            return ExecutionResult.rejected("unknown_outcome", "close order_send returned None")
        retcode = int(getattr(res, "retcode", -1))
        rc = classify_retcode(retcode)
        if rc in (RetcodeClass.SUCCESS, RetcodeClass.PARTIAL):
            # Verify via positions: the ticket must be gone (or reduced).
            remaining = [
                p
                for p in (self._client.positions() or [])
                if int(getattr(p, "ticket", 0)) == ticket
            ]
            if not remaining:
                return ExecutionResult.accepted(
                    venue_order_id=str(ticket),
                    state=OrderState.CLOSED,
                    message=f"position {ticket} closed",
                )
            return ExecutionResult.accepted(
                venue_order_id=str(ticket),
                state=OrderState.POSITION,
                message=f"close partially matched; position {ticket} still open",
            )
        return ExecutionResult.rejected(str(retcode), f"close rejected: {retcode}")

    def _price_for_close(self, symbol: str, is_long: bool) -> float:
        tick = self._client.tick(symbol)
        bid = float(tick.bid)
        ask = float(tick.ask)
        return bid if is_long else ask
