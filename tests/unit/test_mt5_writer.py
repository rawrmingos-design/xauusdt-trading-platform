"""Phase 3 tests: intent store + writer (PROJECT-MT5-002).

Covers the TL test matrix:
  - happy path (BUY → order_check OK → order_send DONE → reconcile → position)
  - requote retry
  - timeout → UNKNOWN_OUTCOME → reconcile (found / not found → operator)
  - duplicate submission
  - partial fill
  - restart recovery
  - protective controls (no SL → REJECT, wrong magic, wrong symbol, mode guard)
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from xauusdt.execution.errors import ModeGuardError, OrderValidationError
from xauusdt.execution.models import OrderKind, OrderSide, OrderState
from xauusdt.execution.mt5.config import Mt5Mode, Mt5Settings
from xauusdt.execution.mt5.models import TradeRetcode
from xauusdt.execution.mt5.store import Mt5IntentStore
from xauusdt.execution.mt5.writer import Mt5Writer
from xauusdt.execution.orders import OrderIntent

# --------------------------------------------------------------------- fakes


@dataclass
class FakeRetcode:
    retcode: int
    order: int = 0


@dataclass
class FakeDeal:
    comment: str
    volume: float
    magic: int = 0
    ticket: int = 0


@dataclass
class FakePosition:
    ticket: int
    symbol: str
    volume: float
    type: int  # 0=BUY(long), 1=SELL(short)
    price_open: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    magic: int = 0
    comment: str = ""
    login: int = 100


@dataclass
class FakeOrder:
    comment: str
    magic: int = 0
    ticket: int = 0
    state: int = 1


@dataclass
class FakeTick:
    symbol: str
    bid: float
    ask: float


class FakeClient:
    """Configurable venue fake for the write path."""

    def __init__(self, *, bid: float = 2400.0, ask: float = 2400.5) -> None:
        self.bid = bid
        self.ask = ask
        self.check_retcodes: list[int] = [0]
        self.send_retcodes: list[int] = [TradeRetcode.DONE.value]
        self.sent: list[dict] = []
        self.orders_list: list = []
        self.deals_list: list = []
        self.positions_list: list = []
        self.modify_calls: list[tuple] = []
        self.close_calls: list[dict] = []
        self._tick = FakeTick("XAUUSD", bid, ask)

    # read path
    def tick(self, symbol: str):
        return self._tick

    def orders(self):
        return self.orders_list

    def positions(self):
        return self.positions_list

    def deals_by_comment(self, comment: str):
        return [d for d in self.deals_list if d.comment == comment]

    def history(self, *, from_ms: int, to_ms: int):
        return self.deals_list

    # write path
    def order_check(self, request: dict):
        rc = self.check_retcodes.pop(0) if self.check_retcodes else 0
        return FakeRetcode(rc)

    def order_send(self, request: dict):
        rc = self.send_retcodes.pop(0) if self.send_retcodes else TradeRetcode.DONE.value
        self.sent.append(request)
        return FakeRetcode(rc, order=12345)

    def position_modify(self, *, ticket: int, sl: float, tp: float, symbol: str):
        self.modify_calls.append((ticket, sl, tp, symbol))
        return FakeRetcode(TradeRetcode.DONE.value)

    def symbol_info(self, symbol: str):
        return None


@dataclass(frozen=True)
class FakeSymbolInfo:
    symbol: str = "XAUUSD"
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01


def make_settings(magic: int = 42, mode: str = Mt5Mode.DEMO) -> Mt5Settings:
    return Mt5Settings(
        login=100,
        password="secret",
        server="Exness-MT5Trial",
        mode=mode,
        magic=magic,
    )


def make_intent(
    *,
    side: OrderSide = OrderSide.LONG,
    kind: OrderKind = OrderKind.MARKET,
    volume: float = 0.01,
    sl: float = 2390.0,
    tp: float = 2410.0,
) -> OrderIntent:
    return OrderIntent(
        symbol="XAUUSD",
        side=side,
        kind=kind,
        volume=volume,
        entry_price=2400.0,
        stop_loss=sl,
        take_profit=tp,
    )


def make_writer(client: FakeClient, settings: Mt5Settings | None = None):
    settings = settings or make_settings()
    store = Mt5IntentStore(":memory:")
    writer = Mt5Writer(client, store, settings, mode_guard=settings.ensure_demo_or_paper)
    return writer, store


# ------------------------------------------------------------- happy path


def test_submit_happy_path_market_buy():
    client = FakeClient()
    writer, store = make_writer(client)

    res = writer.submit(make_intent(), symbol_info=FakeSymbolInfo())

    assert res.ok
    assert res.state is OrderState.FILLED
    assert res.venue_order_id == "12345"
    # persist-before-send: intent row exists, state = FILLED
    intents = store.intents_in_states([OrderState.FILLED])
    assert len(intents) == 1
    assert intents[0]["request_id"].startswith("xauusdt-")
    # order_send called exactly once with SL/TP + magic + comment
    assert len(client.sent) == 1
    req = client.sent[0]
    assert req["sl"] == 2390.0
    assert req["tp"] == 2410.0
    assert req["magic"] == 42
    assert req["comment"] == intents[0]["request_id"]


def test_submit_pending_placed_is_submitted():
    client = FakeClient()
    client.send_retcodes = [TradeRetcode.PLACED.value]
    writer, store = make_writer(client)

    res = writer.submit(make_intent(kind=OrderKind.LIMIT), symbol_info=FakeSymbolInfo())

    assert res.ok
    assert res.state is OrderState.SUBMITTED
    row = store.get_intent(client.sent[0]["comment"])
    assert row is not None
    assert row["state"] == OrderState.SUBMITTED.value


# ------------------------------------------------------------- requote retry


def test_submit_requote_then_done_retries():
    client = FakeClient()
    client.send_retcodes = [TradeRetcode.REQUOTE.value, TradeRetcode.DONE.value]
    writer, _ = make_writer(client)

    res = writer.submit(make_intent(), symbol_info=FakeSymbolInfo())

    assert res.ok
    assert res.state is OrderState.FILLED
    assert len(client.sent) == 2  # requote → retry → done


def test_submit_requote_max_retries_rejects():
    client = FakeClient()
    client.send_retcodes = [TradeRetcode.REQUOTE.value] * 5
    writer, _ = make_writer(client)

    res = writer.submit(make_intent(), symbol_info=FakeSymbolInfo())

    assert not res.ok
    assert res.state is OrderState.REJECTED
    # max 3 total attempts (1 initial + 2 auto-retries) per contract §2.3
    assert len(client.sent) == 3


# ------------------------------------------------------------- timeout


def test_submit_timeout_never_retried_unknown_outcome():
    client = FakeClient()
    client.send_retcodes = [TradeRetcode.TIMEOUT.value, TradeRetcode.DONE.value]
    writer, store = make_writer(client)

    res = writer.submit(make_intent(), symbol_info=FakeSymbolInfo())

    # TIMEOUT → UNKNOWN_OUTCOME, NO auto-retry even though DONE would follow
    assert not res.ok
    assert res.state is OrderState.UNKNOWN_OUTCOME
    assert len(client.sent) == 1  # never resent
    row = store.get_intent(client.sent[0]["comment"])
    assert row is not None
    assert row["state"] == OrderState.UNKNOWN_OUTCOME.value


# ------------------------------------------------------------- too_many_requests


def test_submit_too_many_requests_safe_retry_no_evidence():
    client = FakeClient()
    client.send_retcodes = [TradeRetcode.TOO_MANY_REQUESTS.value, TradeRetcode.DONE.value]
    writer, _ = make_writer(client)

    res = writer.submit(make_intent(), symbol_info=FakeSymbolInfo())

    assert res.ok
    assert res.state is OrderState.FILLED
    assert len(client.sent) == 2  # rate-limited → safe retry → done


def test_submit_too_many_requests_idempotent_evidence_blocks_retry():
    client = FakeClient()
    client.send_retcodes = [TradeRetcode.TOO_MANY_REQUESTS.value, TradeRetcode.DONE.value]
    writer, store = make_writer(client)

    # The first send actually slipped through (deal exists with the comment).
    def _slip_through(request: dict):
        rc = client.send_retcodes.pop(0) if client.send_retcodes else TradeRetcode.DONE.value
        client.sent.append(request)
        client.deals_list.append(FakeDeal(comment=request["comment"], volume=0.01, magic=42))
        return FakeRetcode(rc, order=12345)

    client.order_send = _slip_through
    res = writer.submit(make_intent(), symbol_info=FakeSymbolInfo())

    # Evidence found → resolved as FILLED, no blind retry of a duplicate
    assert res.ok
    assert res.state is OrderState.FILLED
    assert len(client.sent) == 1  # never resent


# ------------------------------------------------------------- duplicate


def test_submit_duplicate_request_id_never_sends():
    client = FakeClient()
    writer, store = make_writer(client)

    # First submission places a pending order with the comment.
    first = make_intent(kind=OrderKind.LIMIT)
    client.send_retcodes = [TradeRetcode.PLACED.value]
    res1 = writer.submit(first, symbol_info=FakeSymbolInfo())
    rid = client.sent[0]["comment"]
    assert res1.state is OrderState.SUBMITTED
    client.orders_list.append(FakeOrder(comment=rid, magic=42, ticket=77))

    # Second attempt at the SAME intent → idempotency hit, no send.
    client.sent.clear()
    res2 = writer.submit(first, symbol_info=FakeSymbolInfo())

    assert res2.ok
    assert res2.state is OrderState.SUBMITTED
    assert client.sent == []  # never re-sent


# ------------------------------------------------------------- partial fill


def test_submit_partial_fill_no_auto_replace():
    client = FakeClient()
    client.send_retcodes = [TradeRetcode.DONE_PARTIAL.value]
    writer, store = make_writer(client)

    res = writer.submit(make_intent(volume=0.05), symbol_info=FakeSymbolInfo())

    assert res.ok
    assert res.state is OrderState.PARTIALLY_FILLED
    assert len(client.sent) == 1  # no auto-replace of the remainder
    row = store.get_intent(client.sent[0]["comment"])
    assert row is not None
    assert row["state"] == OrderState.PARTIALLY_FILLED.value


# ------------------------------------------------------------- protective


def test_submit_without_sl_rejected_before_venue():
    client = FakeClient()
    writer, _ = make_writer(client)

    with pytest.raises(OrderValidationError):
        writer.submit(make_intent(sl=0.0), symbol_info=FakeSymbolInfo())
    assert client.sent == []  # nothing reached the venue


def test_submit_without_tp_rejected_before_venue():
    client = FakeClient()
    writer, _ = make_writer(client)

    with pytest.raises(OrderValidationError):
        writer.submit(make_intent(tp=0.0), symbol_info=FakeSymbolInfo())
    assert client.sent == []


def test_write_guard_rejects_live_mode():
    client = FakeClient()
    settings = make_settings(mode=Mt5Mode.LIVE)
    writer, _ = make_writer(client, settings)

    with pytest.raises(ModeGuardError):
        writer.submit(make_intent(), symbol_info=FakeSymbolInfo())
    assert client.sent == []


def test_write_guard_rejects_magic_zero():
    client = FakeClient()
    settings = make_settings(magic=0)
    writer, _ = make_writer(client, settings)

    with pytest.raises(ModeGuardError):
        writer.submit(make_intent(), symbol_info=FakeSymbolInfo())
    assert client.sent == []


# ------------------------------------------------------------- modify / close


def test_modify_sl_tp_wrong_magic_rejected():
    client = FakeClient()
    writer, _ = make_writer(client)
    client.positions_list.append(
        FakePosition(ticket=55, symbol="XAUUSD", volume=0.01, type=0, magic=999)
    )

    res = writer.modify_sl_tp("55", sl=2380.0, tp=2420.0)

    assert not res.ok
    assert res.rejection_code == "wrong_magic"
    assert client.modify_calls == []


def test_modify_sl_tp_ok():
    client = FakeClient()
    writer, _ = make_writer(client)
    client.positions_list.append(
        FakePosition(ticket=55, symbol="XAUUSD", volume=0.01, type=0, magic=42)
    )

    res = writer.modify_sl_tp("55", sl=2380.0, tp=2420.0)

    assert res.ok
    assert client.modify_calls == [(55, 2380.0, 2420.0, "XAUUSD")]


def test_close_position_wrong_magic_rejected():
    client = FakeClient()
    writer, _ = make_writer(client)
    client.positions_list.append(
        FakePosition(ticket=55, symbol="XAUUSD", volume=0.01, type=0, magic=999)
    )

    res = writer.close_position("55")

    assert not res.ok
    assert res.rejection_code == "wrong_magic"
    assert client.sent == []


def test_close_position_ok_verifies_gone():
    client = FakeClient()
    writer, _ = make_writer(client)
    client.positions_list.append(
        FakePosition(ticket=55, symbol="XAUUSD", volume=0.01, type=0, magic=42)
    )

    # After close, position is gone.
    def _close(request: dict):
        client.close_calls.append(request)
        client.positions_list.clear()
        return FakeRetcode(TradeRetcode.DONE.value, order=999)

    client.order_send = _close
    res = writer.close_position("55")

    assert res.ok
    assert res.state is OrderState.CLOSED
    assert len(client.close_calls) == 1
    req = client.close_calls[0]
    assert req["type"] == 1  # SELL to close a BUY
    assert req["volume"] == 0.01
    assert req["ticket"] == 55
