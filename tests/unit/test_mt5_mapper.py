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
    intent_to_request,
    order_to_domain,
    position_to_domain,
    symbol_to_domain,
    tick_to_domain,
)
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
