"""Tests for MT5 client + adapter with a FAKE MT5 module (PROJECT-MT5-001).

No real terminal required. The fake injects itself into
``xauusdt.execution.mt5.client`` via monkeypatch of the lazy import, so the
adapter/client behavior is tested end-to-end against a simulated venue.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xauusdt.execution.errors import (
    ModeGuardError,
    SymbolUnavailableError,
)
from xauusdt.execution.mt5.adapter import Mt5ExecutionAdapter
from xauusdt.execution.mt5.client import Mt5Client
from xauusdt.execution.mt5.config import Mt5Settings


def _settings(**overrides):
    base = dict(
        login=12345,
        password="secret",
        server="Exness-MT5Trial",
        mode="demo",
    )
    base.update(overrides)
    return Mt5Settings(
        login=int(base["login"]),
        password=str(base["password"]),
        server=str(base["server"]),
        mode=str(base["mode"]),
        symbol=str(base.get("symbol", "")),
        terminal_path=str(base.get("terminal_path", "")),
    )


class FakeMT5:
    """Minimal fake of the MetaTrader5 module (read path)."""

    def __init__(self):
        self.initialized = False
        self.calls = []
        self.symbols = ["EURUSD", "XAUUSD", "GBPUSD"]
        self.account = SimpleNamespace(
            login=12345,
            server="Exness-MT5Trial",
            currency="USD",
            balance=10000.0,
            equity=10050.0,
            margin=100.0,
            margin_free=9900.0,
            leverage=100,
            trade_mode=0,
            name="Trial",
            trade_allowed=True,
        )
        self.symbol = SimpleNamespace(
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
        self.tick = SimpleNamespace(
            symbol="XAUUSD", bid=2400.1, ask=2400.3, time=1_752_800_000_000, volume=1.5
        )
        self.positions = []
        self.orders = []

    def initialize(self, **kwargs):
        self.calls.append(("initialize", kwargs))
        self.initialized = True
        return True

    def shutdown(self):
        self.calls.append(("shutdown",))
        self.initialized = False

    def terminal_info(self):
        return SimpleNamespace(connected=True)

    def account_info(self):
        return self.account

    def symbols_get(self):
        return [SimpleNamespace(name=s) for s in self.symbols]

    def symbol_info(self, symbol):
        if symbol == "XAUUSD":
            return self.symbol
        return None

    def symbol_info_tick(self, symbol):
        return self.tick

    def positions_get(self):
        return self.positions

    def orders_get(self):
        return self.orders

    def history_deals_get(self, f, t):
        return []

    def last_error(self):
        return None


@pytest.fixture
def fake_mt5(monkeypatch):
    fake = FakeMT5()

    def _fake_import(self):  # bound as instance method, receives self
        return fake

    # Inject the fake as the MetaTrader5 module for the client's lazy import.
    import xauusdt.execution.mt5.client as client_mod

    monkeypatch.setattr(client_mod.Mt5Client, "_module", _fake_import)
    return fake


def test_client_connect_disconnect(fake_mt5):
    c = Mt5Client(_settings())
    assert not c.connected
    c.connect()
    assert c.connected
    assert fake_mt5.calls[0][0] == "initialize"
    c.disconnect()
    assert not c.connected
    assert fake_mt5.calls[-1][0] == "shutdown"


def test_client_connect_retries_then_raises(monkeypatch):
    fake = FakeMT5()
    fake.initialize = lambda **kw: False  # type: ignore[method-assign]

    def _fake_import(self):
        return fake

    import xauusdt.execution.mt5.client as client_mod

    monkeypatch.setattr(client_mod.Mt5Client, "_module", _fake_import)
    c = Mt5Client(_settings())
    with pytest.raises(Exception) as ei:
        c.connect()
    assert "init" in str(ei.value).lower()


def test_adapter_mode_guard_blocks_live():
    with pytest.raises(ModeGuardError):
        Mt5ExecutionAdapter(_settings(mode="live"))


def test_adapter_read_path(fake_mt5):
    a = Mt5ExecutionAdapter(_settings())
    a.connect()

    acc = a.account_info()
    assert acc.mode == "DEMO"
    assert acc.equity == 10050.0

    sym = a.resolve_symbol("XAUUSD")
    assert sym == "XAUUSD"

    info = a.symbol_info("XAUUSD")
    assert info.volume_min == 0.01

    t = a.tick("XAUUSD")
    assert t.mid == 2400.2

    assert a.positions() == []
    assert a.orders() == []
    assert a.reconcile() == []

    a.disconnect()


def test_adapter_symbol_auto_discovery(fake_mt5):
    a = Mt5ExecutionAdapter(_settings(symbol=""))
    a.connect()
    sym = a.resolve_symbol("")
    assert sym == "XAUUSD"


def test_adapter_symbol_missing_raises(fake_mt5):
    a = Mt5ExecutionAdapter(_settings(symbol="BTCUSD"))
    a.connect()
    with pytest.raises(SymbolUnavailableError):
        a.resolve_symbol("BTCUSD")


def test_write_path_not_implemented(fake_mt5):
    """Phase 2: write methods must raise, never place an order."""
    from xauusdt.execution.models import OrderKind, OrderSide
    from xauusdt.execution.orders import OrderIntent

    a = Mt5ExecutionAdapter(_settings())
    intent = OrderIntent(
        symbol="XAUUSD",
        side=OrderSide.LONG,
        kind=OrderKind.MARKET,
        volume=0.05,
        entry_price=2400.0,
    )
    with pytest.raises(NotImplementedError):
        a.check_order(intent)
    with pytest.raises(NotImplementedError):
        a.place_order(intent)
    with pytest.raises(NotImplementedError):
        a.close_position("1")


def test_meta_trader5_never_imported_at_module_level():
    """Strategy/risk must never see MetaTrader5; only client.py may import it."""
    import subprocess
    import sys

    # Ensure the execution package imports without the MetaTrader5 package.
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import xauusdt.execution.interface; import xauusdt.execution.models; import xauusdt.execution.orders; print('ok')",
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout

    # The adapter/client must NOT import MetaTrader5 at module level.
    import re

    src = open("src/xauusdt/execution/mt5/client.py", encoding="utf-8").read()
    assert "import MetaTrader5" in src  # lazy import exists
    import_stmt = re.compile(r"^\s*(import MetaTrader5|from MetaTrader5)", re.MULTILINE)
    for path in (
        "src/xauusdt/execution/mt5/adapter.py",
        "src/xauusdt/execution/interface.py",
        "src/xauusdt/execution/orders.py",
        "src/xauusdt/execution/models.py",
    ):
        src2 = open(path, encoding="utf-8").read()
        assert not import_stmt.search(src2), f"{path} imports MetaTrader5 at module level"
