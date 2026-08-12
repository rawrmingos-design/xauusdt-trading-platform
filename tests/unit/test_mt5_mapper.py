"""Tests for MT5 mapper (PROJECT-MT5-001).

Pure mapping functions tested against fake MT5 attribute objects — no real
terminal required.
"""

from __future__ import annotations

from types import SimpleNamespace

from xauusdt.execution.models import (
    OrderKind,
    OrderSide,
    OrderState,
)
from xauusdt.execution.mt5.mapper import (
    account_to_domain,
    intent_to_mt5_order_type,
    intent_to_request,
    order_to_domain,
    position_to_domain,
    request_result_to_domain,
    symbol_to_domain,
    tick_to_domain,
)
from xauusdt.execution.mt5.models import RetcodeClass, TradeRetcode, classify_retcode
from xauusdt.execution.orders import OrderIntent


def _tick():
    return SimpleNamespace(
        symbol="XAUUSD", bid=2400.1, ask=2400.3, time=1_752_800_000_000, volume=1.5
    )


def _symbol():
    return SimpleNamespace(
        name="XAUUSD",
        digits=2,
        point=0.01,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        trade_contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_mode="FULL",
        trade_stops_level=0,
        spread=15.0,
    )


def _account():
    return SimpleNamespace(
        login=12345,
        server="Exness-MT5Trial",
        currency="USD",
        balance=10000.0,
        equity=10050.0,
        margin=100.0,
        margin_free=9900.0,
        leverage=100,
        trade_mode=0,  # DEMO
        name="Trial",
        trade_allowed=True,
    )


def _position():
    return SimpleNamespace(
        ticket=777,
        symbol="XAUUSD",
        type=0,  # BUY
        volume=0.05,
        price_open=2400.0,
        time=1_752_800_000_000,
        sl=2390.0,
        tp=2420.0,
        commission=0.5,
        swap=0.0,
        profit=12.5,
        comment="",
    )


def test_tick_mapping():
    t = tick_to_domain(_tick())
    assert t.symbol == "XAUUSD"
    assert t.bid == 2400.1
    assert t.ask == 2400.3
    assert t.mid == 2400.2
    assert t.time.tzinfo is not None


def test_symbol_mapping():
    s = symbol_to_domain(_symbol())
    assert s.symbol == "XAUUSD"
    assert s.volume_min == 0.01
    assert s.volume_step == 0.01
    assert s.contract_size == 100.0
    assert s.trade_mode == "FULL"


def test_account_mapping_demo_mode():
    a = account_to_domain(_account())
    assert a.login == 12345
    assert a.mode == "DEMO"
    assert a.equity == 10050.0
    assert a.free_margin == 9900.0


def test_account_mapping_live_mode():
    acc = _account()
    acc.trade_mode = 2  # LIVE
    a = account_to_domain(acc)
    assert a.mode == "LIVE"


def test_position_mapping_long():
    p = position_to_domain(_position())
    assert p.position_id == "777"
    assert p.side == OrderSide.LONG
    assert p.volume == 0.05
    assert p.open_price == 2400.0
    assert p.sl == 2390.0
    assert p.profit == 12.5


def test_position_mapping_short():
    pos = _position()
    pos.type = 1  # SELL
    p = position_to_domain(pos)
    assert p.side == OrderSide.SHORT


def test_order_state_mapping():
    assert order_to_domain(SimpleNamespace(state=0)) == OrderState.SUBMITTING
    assert order_to_domain(SimpleNamespace(state=1)) == OrderState.SUBMITTED
    assert order_to_domain(SimpleNamespace(state=2)) == OrderState.REJECTED
    assert order_to_domain(SimpleNamespace(state=3)) == OrderState.PARTIALLY_FILLED
    assert order_to_domain(SimpleNamespace(state=4)) == OrderState.FILLED
    assert order_to_domain(SimpleNamespace(state=5)) == OrderState.REJECTED


def test_intent_to_request_market_buy():
    intent = OrderIntent(
        symbol="XAUUSD",
        side=OrderSide.LONG,
        kind=OrderKind.MARKET,
        volume=0.05,
        entry_price=2400.0,
        stop_loss=2390.0,
        take_profit=2420.0,
    )
    req = intent_to_request(intent, action=1, price=2400.5)
    assert req.symbol == "XAUUSD"
    assert req.volume == 0.05
    assert req.type == 0  # BUY
    assert req.sl == 2390.0
    assert req.tp == 2420.0


def test_intent_to_request_market_sell():
    intent = OrderIntent(
        symbol="XAUUSD",
        side=OrderSide.SHORT,
        kind=OrderKind.MARKET,
        volume=0.05,
        entry_price=2400.0,
    )
    req = intent_to_request(intent, action=1, price=2399.8)
    assert req.type == 1  # SELL


# --------------------------------------------------------------------------
# OrderKind -> MT5 order type: all six side/kind combinations
# --------------------------------------------------------------------------


def _intent(kind: OrderKind, side: OrderSide) -> OrderIntent:
    return OrderIntent(
        symbol="XAUUSD",
        side=side,
        kind=kind,
        volume=0.05,
        entry_price=2400.0,
    )


def test_order_type_long_market_buy():
    assert intent_to_mt5_order_type(_intent(OrderKind.MARKET, OrderSide.LONG)) == 0


def test_order_type_short_market_sell():
    assert intent_to_mt5_order_type(_intent(OrderKind.MARKET, OrderSide.SHORT)) == 1


def test_order_type_long_limit_buy_limit():
    assert intent_to_mt5_order_type(_intent(OrderKind.LIMIT, OrderSide.LONG)) == 2


def test_order_type_short_limit_sell_limit():
    assert intent_to_mt5_order_type(_intent(OrderKind.LIMIT, OrderSide.SHORT)) == 3


def test_order_type_long_stop_buy_stop():
    assert intent_to_mt5_order_type(_intent(OrderKind.STOP, OrderSide.LONG)) == 4


def test_order_type_short_stop_sell_stop():
    assert intent_to_mt5_order_type(_intent(OrderKind.STOP, OrderSide.SHORT)) == 5


def test_order_type_all_six_combinations():
    expected = {
        (OrderKind.MARKET, OrderSide.LONG): 0,
        (OrderKind.MARKET, OrderSide.SHORT): 1,
        (OrderKind.LIMIT, OrderSide.LONG): 2,
        (OrderKind.LIMIT, OrderSide.SHORT): 3,
        (OrderKind.STOP, OrderSide.LONG): 4,
        (OrderKind.STOP, OrderSide.SHORT): 5,
    }
    for (kind, side), want in expected.items():
        assert intent_to_mt5_order_type(_intent(kind, side)) == want, (kind, side)


# --------------------------------------------------------------------------
# Retcode classification (official MQL5 ENUM_TRADE_RETCODE)
# --------------------------------------------------------------------------


def test_retcode_classification_success():
    assert classify_retcode(TradeRetcode.DONE.value) is RetcodeClass.SUCCESS
    assert classify_retcode(TradeRetcode.PLACED.value) is RetcodeClass.SUCCESS


def test_retcode_official_values_exact():
    """Lock the exact numeric values to the official MQL5 documentation."""
    official = {
        "REQUOTE": 10004,
        "REJECT": 10006,
        "CANCEL": 10007,
        "PLACED": 10008,
        "DONE": 10009,
        "DONE_PARTIAL": 10010,
        "ERROR": 10011,
        "TIMEOUT": 10012,
        "INVALID": 10013,
        "INVALID_VOLUME": 10014,
        "INVALID_PRICE": 10015,
        "INVALID_STOPS": 10016,
        "TRADE_DISABLED": 10017,
        "MARKET_CLOSED": 10018,
        "NO_MONEY": 10019,
        "PRICE_CHANGED": 10020,
        "PRICE_OFF": 10021,
        "INVALID_EXPIRATION": 10022,
        "ORDER_CHANGED": 10023,
        "TOO_MANY_REQUESTS": 10024,
        "NO_CHANGES": 10025,
        "SERVER_DISABLES_AT": 10026,
        "CLIENT_DISABLES_AT": 10027,
        "LOCKED": 10028,
        "FROZEN": 10029,
        "INVALID_FILL": 10030,
        "CONNECTION": 10031,
        "ONLY_REAL": 10032,
        "LIMIT_ORDERS": 10033,
        "LIMIT_VOLUME": 10034,
        "INVALID_ORDER": 10035,
        "POSITION_CLOSED": 10036,
        "INVALID_CLOSE_VOLUME": 10038,
        "CLOSE_ORDER_EXIST": 10039,
        "LIMIT_POSITIONS": 10040,
        "REJECT_CANCEL": 10041,
        "LONG_ONLY": 10042,
        "SHORT_ONLY": 10043,
        "CLOSE_ONLY": 10044,
        "FIFO_CLOSE": 10045,
        "HEDGE_PROHIBITED": 10046,
    }
    for name, value in official.items():
        member = getattr(TradeRetcode, name)
        assert member.value == value, f"{name} should be {value}, got {member.value}"


def test_retcode_classification_exact_mapping():
    """Explicit classification per official retcode number."""
    cases = {
        10004: RetcodeClass.RETRYABLE,  # REQUOTE
        10006: RetcodeClass.REJECTED,  # REJECT
        10007: RetcodeClass.REJECTED,  # CANCEL
        10008: RetcodeClass.SUCCESS,  # PLACED
        10009: RetcodeClass.SUCCESS,  # DONE
        10010: RetcodeClass.PARTIAL,  # DONE_PARTIAL
        10011: RetcodeClass.REJECTED,  # ERROR
        10012: RetcodeClass.RETRYABLE,  # TIMEOUT
        10013: RetcodeClass.REJECTED,  # INVALID
        10014: RetcodeClass.REJECTED,  # INVALID_VOLUME
        10015: RetcodeClass.REJECTED,  # INVALID_PRICE
        10016: RetcodeClass.REJECTED,  # INVALID_STOPS
        10017: RetcodeClass.VENUE_ERROR,  # TRADE_DISABLED
        10018: RetcodeClass.VENUE_ERROR,  # MARKET_CLOSED
        10019: RetcodeClass.REJECTED,  # NO_MONEY
        10020: RetcodeClass.RETRYABLE,  # PRICE_CHANGED
        10021: RetcodeClass.RETRYABLE,  # PRICE_OFF
        10024: RetcodeClass.RETRYABLE,  # TOO_MANY_REQUESTS
        10026: RetcodeClass.VENUE_ERROR,  # SERVER_DISABLES_AT
        10027: RetcodeClass.VENUE_ERROR,  # CLIENT_DISABLES_AT
        10028: RetcodeClass.VENUE_ERROR,  # LOCKED
        10029: RetcodeClass.VENUE_ERROR,  # FROZEN
        10030: RetcodeClass.REJECTED,  # INVALID_FILL
        10031: RetcodeClass.VENUE_ERROR,  # CONNECTION
        10032: RetcodeClass.VENUE_ERROR,  # ONLY_REAL
        10035: RetcodeClass.REJECTED,  # INVALID_ORDER
        10036: RetcodeClass.VENUE_ERROR,  # POSITION_CLOSED
    }
    for code, want in cases.items():
        assert classify_retcode(code) is want, (
            f"retcode {code}: expected {want.value}, got {classify_retcode(code).value}"
        )


def test_retcode_classification_unknown_is_never_success():
    assert classify_retcode(99999) is RetcodeClass.UNKNOWN
    assert classify_retcode(0) is RetcodeClass.UNKNOWN
    assert classify_retcode(-1) is RetcodeClass.UNKNOWN
    assert classify_retcode(10005) is RetcodeClass.UNKNOWN  # unused official gap
    assert classify_retcode(10037) is RetcodeClass.UNKNOWN  # unused official gap


def test_retcode_classification_partial():
    assert classify_retcode(TradeRetcode.DONE_PARTIAL.value) is RetcodeClass.PARTIAL


def test_retcode_classification_retryable():
    for rc in (
        TradeRetcode.REQUOTE,
        TradeRetcode.PRICE_CHANGED,
        TradeRetcode.PRICE_OFF,
        TradeRetcode.TIMEOUT,
    ):
        assert classify_retcode(rc.value) is RetcodeClass.RETRYABLE, rc


def test_retcode_classification_rejected():
    for rc in (
        TradeRetcode.REJECT,
        TradeRetcode.INVALID_VOLUME,
        TradeRetcode.INVALID_PRICE,
        TradeRetcode.INVALID_STOPS,
        TradeRetcode.NO_MONEY,
    ):
        assert classify_retcode(rc.value) is RetcodeClass.REJECTED, rc


def test_retcode_classification_venue_error():
    for rc in (
        TradeRetcode.TRADE_DISABLED,
        TradeRetcode.MARKET_CLOSED,
    ):
        assert classify_retcode(rc.value) is RetcodeClass.VENUE_ERROR, rc


def test_request_result_done_is_filled():
    res = request_result_to_domain(TradeRetcode.DONE.value, comment="ok", order_id=7)
    assert res.ok
    assert res.state == OrderState.FILLED
    assert res.venue_order_id == "7"


def test_request_result_placed_is_submitted():
    res = request_result_to_domain(TradeRetcode.PLACED.value, comment="placed", order_id=8)
    assert res.ok
    assert res.state == OrderState.SUBMITTED


def test_request_result_done_partial_is_accepted_partial():
    res = request_result_to_domain(TradeRetcode.DONE_PARTIAL.value, order_id=9)
    assert res.ok
    assert res.state == OrderState.PARTIALLY_FILLED


def test_request_result_requote_is_rejected_retryable():
    res = request_result_to_domain(TradeRetcode.REQUOTE.value)
    assert not res.ok
    assert res.rejection_code == "retryable"
    assert res.state == OrderState.REJECTED


def test_request_result_invalid_volume_code():
    res = request_result_to_domain(TradeRetcode.INVALID_VOLUME.value)
    assert not res.ok
    assert res.rejection_code == "invalid_volume"


def test_request_result_market_closed_venue_error():
    res = request_result_to_domain(TradeRetcode.MARKET_CLOSED.value)
    assert not res.ok
    assert res.rejection_code == "venue_error"


def test_request_result_unknown_is_rejected_not_success():
    res = request_result_to_domain(99999)
    assert not res.ok
    assert res.rejection_code == "unknown_retcode_99999"
    assert res.state == OrderState.REJECTED
