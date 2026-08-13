"""Domain ↔ MT5 mapping (PROJECT-MT5-001).

Pure functions translating between venue-free domain models
(``execution/models.py``) and raw MT5 objects. No MetaTrader5 import here:
callers pass plain attribute-accessible objects (namedtuples from the real
module, or fakes in tests).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from xauusdt.execution.models import (
    AccountSnapshot,
    MarketTick,
    OrderKind,
    OrderSide,
    OrderState,
    PositionSnapshot,
    SymbolInfo,
)
from xauusdt.execution.mt5.models import (
    Mt5OrderRequest,
    RetcodeClass,
    TradeRetcode,
    classify_retcode,
)
from xauusdt.execution.orders import ExecutionResult, OrderIntent


def _to_utc_dt(ms: int | None) -> datetime:
    """Convert epoch-milliseconds to timezone-aware UTC datetime."""
    if not ms:
        return datetime.fromtimestamp(0, tz=UTC)
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def account_to_domain(acc: Any) -> AccountSnapshot:
    """Map MT5 account_info() -> AccountSnapshot."""
    mode = getattr(acc, "trade_mode", 0)
    # MT5 trade modes: 0=DEMO, 1=CONTEST, 2=LIVE
    mode_str = {0: "DEMO", 1: "CONTEST", 2: "LIVE"}.get(mode, "UNKNOWN")
    return AccountSnapshot(
        login=int(getattr(acc, "login", 0)),
        server=str(getattr(acc, "server", "") or ""),
        currency=str(getattr(acc, "currency", "") or ""),
        balance=float(getattr(acc, "balance", 0.0)),
        equity=float(getattr(acc, "equity", 0.0)),
        margin=float(getattr(acc, "margin", 0.0)),
        free_margin=float(getattr(acc, "margin_free", 0.0)),
        leverage=int(getattr(acc, "leverage", 0)),
        mode=mode_str,
        name=str(getattr(acc, "name", "") or ""),
        trade_allowed=bool(getattr(acc, "trade_allowed", True)),
    )


def symbol_to_domain(sym: Any) -> SymbolInfo:
    """Map MT5 symbol_info() -> SymbolInfo (trading constraints)."""
    return SymbolInfo(
        symbol=str(getattr(sym, "name", "") or ""),
        digits=int(getattr(sym, "digits", 0)),
        point=float(getattr(sym, "point", 0.0)),
        tick_size=float(getattr(sym, "trade_tick_size", 0.0)),
        tick_value=float(getattr(sym, "trade_tick_value", 0.0)),
        contract_size=float(getattr(sym, "trade_contract_size", 0.0)),
        volume_min=float(getattr(sym, "volume_min", 0.0)),
        volume_max=float(getattr(sym, "volume_max", 0.0)),
        volume_step=float(getattr(sym, "volume_step", 0.0)),
        trade_mode=str(getattr(sym, "trade_mode", "FULL") or "FULL"),
        stop_level=int(getattr(sym, "trade_stops_level", 0)),
        spread=float(getattr(sym, "spread", 0.0)),
    )


def tick_to_domain(tick: Any) -> MarketTick:
    """Map MT5 symbol_info_tick() -> MarketTick."""
    return MarketTick(
        symbol=str(getattr(tick, "symbol", "") or ""),
        bid=float(getattr(tick, "bid", 0.0)),
        ask=float(getattr(tick, "ask", 0.0)),
        time=_to_utc_dt(getattr(tick, "time", 0)),
        volume=float(getattr(tick, "volume", 0.0)),
    )


def position_to_domain(pos: Any) -> PositionSnapshot:
    """Map MT5 position -> PositionSnapshot (venue ticket -> string id)."""
    side = OrderSide.LONG if int(getattr(pos, "type", 0)) == 0 else OrderSide.SHORT
    return PositionSnapshot(
        position_id=str(getattr(pos, "ticket", 0)),
        symbol=str(getattr(pos, "symbol", "") or ""),
        side=side,
        volume=float(getattr(pos, "volume", 0.0)),
        open_price=float(getattr(pos, "price_open", 0.0)),
        open_time=_to_utc_dt(getattr(pos, "time", 0)),
        sl=float(getattr(pos, "sl", 0.0)),
        tp=float(getattr(pos, "tp", 0.0)),
        commission=float(getattr(pos, "commission", 0.0)),
        swap=float(getattr(pos, "swap", 0.0)),
        profit=float(getattr(pos, "profit", 0.0)),
        comment=str(getattr(pos, "comment", "") or ""),
    )


def order_to_domain(order: Any) -> OrderState:
    """Map MT5 order -> domain OrderState (pending orders = SUBMITTED)."""
    # MT5 order states: 0=STARTED,1=PLACED,2=CANCELED,3=PARTIAL,4=FILLED,5=REJECTED
    state = int(getattr(order, "state", 1))
    mapping = {
        0: OrderState.SUBMITTING,
        1: OrderState.SUBMITTED,
        2: OrderState.REJECTED,
        3: OrderState.PARTIALLY_FILLED,
        4: OrderState.FILLED,
        5: OrderState.REJECTED,
    }
    return mapping.get(state, OrderState.SUBMITTED)


def intent_to_request(intent: OrderIntent, action: int, price: float) -> Mt5OrderRequest:
    """Map domain OrderIntent -> MT5 order request payload (write path).

    MQL5 order types (ENUM_ORDER_TYPE):
        BUY=0, SELL=1, BUY_LIMIT=2, SELL_LIMIT=3, BUY_STOP=4, SELL_STOP=5
    """
    order_type = intent_to_mt5_order_type(intent)
    return Mt5OrderRequest(
        action=action,
        symbol=intent.symbol,
        volume=intent.volume,
        type=order_type,
        price=price,
        sl=intent.stop_loss,
        tp=intent.take_profit,
        comment=intent.comment,
        magic=0,
    )


def intent_to_mt5_order_type(intent: OrderIntent) -> int:
    """Map (side, kind) to the exact MQL5 order type.

    LONG  + MARKET -> BUY        (0)
    SHORT + MARKET -> SELL       (1)
    LONG  + LIMIT  -> BUY_LIMIT  (2)
    SHORT + LIMIT  -> SELL_LIMIT (3)
    LONG  + STOP   -> BUY_STOP   (4)
    SHORT + STOP   -> SELL_STOP  (5)
    """
    if intent.kind == OrderKind.MARKET:
        return 0 if intent.side == OrderSide.LONG else 1
    if intent.kind == OrderKind.LIMIT:
        return 2 if intent.side == OrderSide.LONG else 3
    if intent.kind == OrderKind.STOP:
        return 4 if intent.side == OrderSide.LONG else 5
    raise ValueError(f"unsupported order kind {intent.kind!r}")


def request_result_to_domain(retcode: int, comment: str = "", order_id: int = 0) -> ExecutionResult:
    """Map MT5 order_check/order_send retcode -> ExecutionResult.

    Uses the official MQL5 retcode classification. Unknown codes are NEVER
    treated as success.
    """
    rc = classify_retcode(retcode)
    if rc is RetcodeClass.SUCCESS:
        state = OrderState.FILLED if retcode == TradeRetcode.DONE.value else OrderState.SUBMITTED
        return ExecutionResult.accepted(
            venue_order_id=str(order_id),
            state=state,
            message=comment,
        )
    if rc is RetcodeClass.PARTIAL:
        return ExecutionResult.accepted(
            venue_order_id=str(order_id),
            state=OrderState.PARTIALLY_FILLED,
            message=comment,
        )
    code = _rejection_code(retcode, rc)
    return ExecutionResult.rejected(
        code=code,
        message=comment or f"MT5 retcode {retcode} ({rc.value})",
        state=OrderState.REJECTED,
    )


def _rejection_code(retcode: int, rc: RetcodeClass) -> str:
    if rc is RetcodeClass.RETRYABLE:
        return "retryable"
    if rc is RetcodeClass.VENUE_ERROR:
        return "venue_error"
    if rc is RetcodeClass.UNKNOWN:
        return f"unknown_retcode_{retcode}"
    # REJECTED (or anything classified as rejected)
    try:
        name = TradeRetcode(retcode).name.lower()
    except ValueError:
        name = "rejected"
    return name
