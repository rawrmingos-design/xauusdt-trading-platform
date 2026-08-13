"""Phase 3 tests: reconciler + adapter startup recovery (PROJECT-MT5-002).

Covers:
  - reconcile: deals > positions > orders evidence priority
  - timeout reconcile found → resolve; not found → operator (FAILED_UNKNOWN)
  - restart recovery via startup_reconcile
  - strict position adoption (account + magic namespace + symbol triple-match)
"""

from __future__ import annotations

from dataclasses import dataclass

from xauusdt.execution.models import OrderKind, OrderSide, OrderState
from xauusdt.execution.mt5.adapter import Mt5ExecutionAdapter
from xauusdt.execution.mt5.config import Mt5Mode, Mt5Settings
from xauusdt.execution.mt5.reconcile import Mt5Reconciler
from xauusdt.execution.mt5.store import Mt5IntentStore

MAGIC_BASE = 1000
MAGIC_SPAN = 100


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
    type: int
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
class FakeAccount:
    login: int = 100
    trade_mode: int = 0  # 0 = DEMO


class FakeClient:
    def __init__(self) -> None:
        self.positions_list: list = []
        self.orders_list: list = []
        self.deals_list: list = []
        self.account = FakeAccount()

    def positions(self):
        return self.positions_list

    def orders(self):
        return self.orders_list

    def history(self, *, from_ms: int, to_ms: int):
        return self.deals_list

    def account_info(self):
        return self.account


def make_store() -> Mt5IntentStore:
    store = Mt5IntentStore(":memory:")
    store.set_magic_namespace(MAGIC_BASE, MAGIC_SPAN)
    return store


def make_settings() -> Mt5Settings:
    return Mt5Settings(
        login=100,
        password="secret",
        server="Exness-MT5Trial",
        mode=Mt5Mode.DEMO,
        magic=MAGIC_BASE,
        symbol="XAUUSD",
    )


# ------------------------------------------------------------------ reconcile


def test_reconcile_deals_resolve_filled_with_namespace():
    """Namespace must be configured for adoption to recognize our magic."""
    client = FakeClient()
    store = make_store()
    rid = store.create_intent(
        magic=MAGIC_BASE,
        symbol="XAUUSD",
        side=OrderSide.LONG,
        kind=OrderKind.MARKET,
        volume=0.01,
        entry_price=2400.0,
        stop_loss=2390.0,
        take_profit=2410.0,
        run_id="test-run",
    )
    store.update_state(rid, OrderState.UNKNOWN_OUTCOME)
    client.deals_list.append(FakeDeal(comment=rid, volume=0.01, magic=MAGIC_BASE))

    reconciler = Mt5Reconciler(client)
    reconciler.set_namespace(MAGIC_BASE, MAGIC_SPAN)
    report = reconciler.reconcile(store.intents_in_states([OrderState.UNKNOWN_OUTCOME]))

    assert report.resolved[rid] is OrderState.FILLED
    assert rid not in report.unresolved


def test_reconcile_timeout_not_found_goes_operator():
    client = FakeClient()
    store = make_store()
    rid = store.create_intent(
        magic=MAGIC_BASE,
        symbol="XAUUSD",
        side=OrderSide.LONG,
        kind=OrderKind.MARKET,
        volume=0.01,
        entry_price=2400.0,
        stop_loss=2390.0,
        take_profit=2410.0,
        run_id="test-run",
    )
    store.update_state(rid, OrderState.UNKNOWN_OUTCOME)

    report = Mt5Reconciler(client).reconcile(store.intents_in_states([OrderState.UNKNOWN_OUTCOME]))

    assert report.unresolved[rid] == "no_evidence_after_timeout"


def test_reconcile_submitting_crash_before_send_resets_created():
    client = FakeClient()
    store = make_store()
    rid = store.create_intent(
        magic=MAGIC_BASE,
        symbol="XAUUSD",
        side=OrderSide.LONG,
        kind=OrderKind.MARKET,
        volume=0.01,
        entry_price=2400.0,
        stop_loss=2390.0,
        take_profit=2410.0,
        run_id="test-run",
    )
    store.update_state(rid, OrderState.SUBMITTING)  # crashed mid-submit

    report = Mt5Reconciler(client).reconcile(store.intents_in_states([OrderState.SUBMITTING]))

    assert report.resolved[rid] is OrderState.CREATED  # safe to resubmit


def test_reconcile_ignores_other_magic():
    client = FakeClient()
    store = make_store()
    rid = store.create_intent(
        magic=MAGIC_BASE,
        symbol="XAUUSD",
        side=OrderSide.LONG,
        kind=OrderKind.MARKET,
        volume=0.01,
        entry_price=2400.0,
        stop_loss=2390.0,
        take_profit=2410.0,
        run_id="test-run",
    )
    store.update_state(rid, OrderState.UNKNOWN_OUTCOME)
    # A deal with the same comment but FOREIGN magic must NOT resolve us.
    client.deals_list.append(FakeDeal(comment=rid, volume=0.01, magic=9999))

    report = Mt5Reconciler(client).reconcile(store.intents_in_states([OrderState.UNKNOWN_OUTCOME]))

    assert rid in report.unresolved


# ------------------------------------------------------------------ adoption


def test_adoption_strict_triple_match():
    client = FakeClient()
    store = make_store()
    adapter = Mt5ExecutionAdapter(make_settings(), store=store)
    # bypass connect: inject the client
    adapter._client = client  # type: ignore[assignment]

    client.positions_list = [
        FakePosition(ticket=1, symbol="XAUUSD", volume=0.01, type=0, magic=MAGIC_BASE, login=100),
        FakePosition(
            ticket=2, symbol="XAUUSD", volume=0.01, type=0, magic=MAGIC_BASE + 50, login=100
        ),
        FakePosition(
            ticket=3, symbol="XAUUSD", volume=0.01, type=0, magic=9999, login=100
        ),  # foreign magic
        FakePosition(
            ticket=4, symbol="XAUUSD", volume=0.01, type=0, magic=MAGIC_BASE, login=200
        ),  # other account
        FakePosition(
            ticket=5, symbol="EURUSD", volume=0.01, type=0, magic=MAGIC_BASE, login=100
        ),  # wrong symbol
    ]

    adopted = adapter._adopt_positions()

    assert adopted == ["1", "2"]  # only correct account + magic namespace + symbol


# ------------------------------------------------------------ restart recovery


def test_startup_reconcile_after_crash_mid_send():
    """Crash AFTER order_send but BEFORE persist: restart must resolve via deals."""
    client = FakeClient()
    store = make_store()
    # Simulate: intent persisted (SUBMITTING), send happened at venue (deal exists),
    # then process crashed before update_state(FILLED).
    rid = store.create_intent(
        magic=MAGIC_BASE,
        symbol="XAUUSD",
        side=OrderSide.LONG,
        kind=OrderKind.MARKET,
        volume=0.01,
        entry_price=2400.0,
        stop_loss=2390.0,
        take_profit=2410.0,
        run_id="test-run",
    )
    store.update_state(rid, OrderState.SUBMITTING)
    client.deals_list.append(FakeDeal(comment=rid, volume=0.01, magic=MAGIC_BASE))

    adapter = Mt5ExecutionAdapter(make_settings(), store=store)
    adapter._client = client  # type: ignore[assignment]
    adapter._reconciler._client = client  # type: ignore[assignment]
    adapter._reconciler.set_namespace(MAGIC_BASE, MAGIC_SPAN)

    report = adapter.startup_reconcile()

    assert report.resolved[rid] is OrderState.FILLED  # deal evidence wins
    row = store.get_intent(rid)
    assert row is not None
    assert row["state"] == OrderState.FILLED.value


def test_startup_reconcile_crash_before_send_safe_reset():
    """Crash BEFORE order_send: intent resets to CREATED, safe to re-run."""
    client = FakeClient()
    store = make_store()
    rid = store.create_intent(
        magic=MAGIC_BASE,
        symbol="XAUUSD",
        side=OrderSide.LONG,
        kind=OrderKind.MARKET,
        volume=0.01,
        entry_price=2400.0,
        stop_loss=2390.0,
        take_profit=2410.0,
        run_id="test-run",
    )
    store.update_state(rid, OrderState.SUBMITTING)  # crashed before send

    adapter = Mt5ExecutionAdapter(make_settings(), store=store)
    adapter._client = client  # type: ignore[assignment]
    adapter._reconciler._client = client  # type: ignore[assignment]
    adapter._reconciler.set_namespace(MAGIC_BASE, MAGIC_SPAN)

    report = adapter.startup_reconcile()

    assert report.resolved[rid] is OrderState.CREATED  # safe to resubmit
    row = store.get_intent(rid)
    assert row is not None
    assert row["state"] == OrderState.CREATED.value


def test_startup_reconcile_adopts_orphan_position():
    """A position at the venue with our magic but NO intent row (orphan) is adopted."""
    client = FakeClient()
    store = make_store()
    client.positions_list.append(
        FakePosition(ticket=77, symbol="XAUUSD", volume=0.01, type=0, magic=MAGIC_BASE, login=100)
    )

    adapter = Mt5ExecutionAdapter(make_settings(), store=store)
    adapter._client = client  # type: ignore[assignment]
    adapter._reconciler.set_namespace(MAGIC_BASE, MAGIC_SPAN)

    report = adapter.startup_reconcile()

    assert "77" in report.open_positions  # adopted
    assert report.unresolved == {}  # nothing needs operator
