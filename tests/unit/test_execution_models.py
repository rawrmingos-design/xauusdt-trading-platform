"""Tests for execution domain models + order state machine (PROJECT-MT5-001)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from xauusdt.execution.models import (
    AccountSnapshot,
    MarketTick,
    OrderKind,
    OrderSide,
    OrderState,
    PositionSnapshot,
    SymbolInfo,
    order_can_transition,
)
from xauusdt.execution.orders import ExecutionResult, OrderIntent


def test_order_state_machine_happy_path():
    path = [
        OrderState.CREATED,
        OrderState.VALIDATED,
        OrderState.RISK_APPROVED,
        OrderState.SUBMITTING,
        OrderState.SUBMITTED,
        OrderState.FILLED,
        OrderState.POSITION,
        OrderState.CLOSED,
    ]
    for src, dst in zip(path, path[1:]):
        assert order_can_transition(src, dst), f"{src} -> {dst}"


def test_order_state_machine_rejects_illegal_jumps():
    assert not order_can_transition(OrderState.CREATED, OrderState.FILLED)
    assert not order_can_transition(OrderState.FILLED, OrderState.SUBMITTED)
    assert not order_can_transition(OrderState.REJECTED, OrderState.SUBMITTED)
    assert not order_can_transition(OrderState.CLOSED, OrderState.POSITION)


def test_order_state_machine_rejection_paths():
    for src in (OrderState.CREATED, OrderState.VALIDATED, OrderState.RISK_APPROVED):
        assert order_can_transition(src, OrderState.REJECTED)
    assert order_can_transition(OrderState.SUBMITTED, OrderState.REJECTED)
    assert order_can_transition(OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED)
    assert order_can_transition(OrderState.PARTIALLY_FILLED, OrderState.FILLED)


def test_order_intent_roundtrip_dict():
    intent = OrderIntent(
        symbol="XAUUSD",
        side=OrderSide.LONG,
        kind=OrderKind.MARKET,
        volume=0.05,
        entry_price=2400.0,
        stop_loss=2390.0,
        take_profit=2420.0,
        created_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        client_order_id="abc-123",
    )
    d = intent.to_dict()
    assert d["symbol"] == "XAUUSD"
    assert d["side"] == "LONG"
    assert d["kind"] == "MARKET"
    assert d["volume"] == 0.05
    assert d["client_order_id"] == "abc-123"


def test_execution_result_rejected_and_accepted():
    rej = ExecutionResult.rejected("margin_insufficient", "not enough margin")
    assert not rej.ok
    assert rej.state == OrderState.REJECTED
    assert rej.rejection_code == "margin_insufficient"

    acc = ExecutionResult.accepted("ticket-1", OrderState.SUBMITTED, filled_price=2400.5)
    assert acc.ok
    assert acc.venue_order_id == "ticket-1"
    assert acc.state == OrderState.SUBMITTED


def test_symbol_info_normalize_volume_respects_grid():
    info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )
    assert info.normalize_volume(0.005) == 0.01  # below min -> min
    assert info.normalize_volume(500.0) == 100.0  # above max -> max
    assert info.normalize_volume(0.123) == pytest.approx(0.12)  # grid step


def test_market_tick_mid():
    t = MarketTick(
        symbol="XAUUSD",
        bid=2400.0,
        ask=2400.4,
        time=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
    )
    assert t.mid == pytest.approx(2400.2)


def test_position_snapshot_to_dict():
    pos = PositionSnapshot(
        position_id="ticket-99",
        symbol="XAUUSD",
        side=OrderSide.LONG,
        volume=0.05,
        open_price=2400.0,
        open_time=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
    )
    d = pos.to_dict()
    assert d["position_id"] == "ticket-99"
    assert d["side"] == "LONG"
    assert d["volume"] == 0.05


def test_account_snapshot_mode_explicit():
    acc = AccountSnapshot(
        login=1234,
        server="Exness-MT5Trial",
        currency="USD",
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        free_margin=10000.0,
        leverage=100,
        mode="DEMO",
    )
    assert acc.mode == "DEMO"
    assert acc.trade_allowed


def test_order_intent_created_at_utc_aware():
    """OrderIntent.created_at must be timezone-aware UTC, never naive."""
    from datetime import timedelta

    from xauusdt.execution.orders import OrderIntent

    intent = OrderIntent(
        symbol="XAUUSD",
        side=OrderSide.LONG,
        kind=OrderKind.MARKET,
        volume=0.05,
        entry_price=2400.0,
    )
    assert intent.created_at.tzinfo is not None
    assert intent.created_at.utcoffset() == timedelta(0)
    assert intent.created_at.tzinfo is UTC
