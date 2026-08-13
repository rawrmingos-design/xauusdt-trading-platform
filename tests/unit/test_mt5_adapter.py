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
from xauusdt.execution.models import SymbolInfo
from xauusdt.execution.mt5.adapter import Mt5ExecutionAdapter
from xauusdt.execution.mt5.client import Mt5Client
from xauusdt.execution.mt5.config import (
    AccountMode,
    ExecutionEnvironment,
    Mt5Settings,
)


def _settings(**overrides):
    base = dict(
        login=12345,
        password="secret",
        server="Exness-MT5Trial",
        mode="demo",
        magic=42,
    )
    base.update(overrides)
    return Mt5Settings(
        login=int(base["login"]),
        password=str(base["password"]),
        server=str(base["server"]),
        mode=str(base["mode"]),
        symbol=str(base.get("symbol", "")),
        terminal_path=str(base.get("terminal_path", "")),
        magic=int(base.get("magic", 42)),
        run_id=str(base.get("run_id", "test-run")),
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
    """Phase 3: write path is implemented — close on an empty venue returns
    position_not_found; it never sends or raises NotImplementedError."""
    a = Mt5ExecutionAdapter(_settings())
    a.connect()  # verifies demo environment, populates _env
    # close on an empty venue → position_not_found (no venue write attempted)
    res = a.close_position("1")
    assert not res.ok
    assert res.rejection_code == "position_not_found"


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


# --------------------------------------------------------------------------
# Symbol discovery — fail-safe, deterministic, ambiguity -> error
# --------------------------------------------------------------------------


def test_resolve_symbol_explicit_exists(fake_mt5):
    a = Mt5ExecutionAdapter(_settings(symbol="XAUUSD"))
    a.connect()
    assert a.resolve_symbol("XAUUSD") == "XAUUSD"


def test_resolve_symbol_explicit_unknown_raises(fake_mt5):
    a = Mt5ExecutionAdapter(_settings(symbol="XAUUSDT"))
    a.connect()
    with pytest.raises(SymbolUnavailableError):
        a.resolve_symbol("XAUUSDT")


def test_resolve_symbol_explicit_trade_disabled_raises(fake_mt5):
    fake_mt5.symbol.trade_mode = "NO"
    a = Mt5ExecutionAdapter(_settings(symbol="XAUUSD"))
    a.connect()
    with pytest.raises(SymbolUnavailableError):
        a.resolve_symbol("XAUUSD")


def test_resolve_symbol_discovery_exact_single(fake_mt5):
    fake_mt5.symbols = ["EURUSD", "XAUUSD", "GBPUSD"]
    a = Mt5ExecutionAdapter(_settings(symbol=""))
    a.connect()
    assert a.resolve_symbol("") == "XAUUSD"


def test_resolve_symbol_discovery_suffix_single(fake_mt5):
    fake_mt5.symbols = ["EURUSD", "XAUUSDm", "GBPUSD"]
    a = Mt5ExecutionAdapter(_settings(symbol=""))
    a.connect()
    assert a.resolve_symbol("") == "XAUUSDm"


def test_resolve_symbol_discovery_multiple_candidates_fails_closed(fake_mt5):
    fake_mt5.symbols = ["XAUUSD", "XAUUSDm", "XAUUSDc", "GOLD"]
    a = Mt5ExecutionAdapter(_settings(symbol=""))
    a.connect()
    with pytest.raises(SymbolUnavailableError) as ei:
        a.resolve_symbol("")
    assert "expected exactly one" in str(ei.value)


def test_resolve_symbol_discovery_none_fails_closed(fake_mt5):
    fake_mt5.symbols = ["EURUSD", "GBPUSD"]
    a = Mt5ExecutionAdapter(_settings(symbol=""))
    a.connect()
    with pytest.raises(SymbolUnavailableError) as ei:
        a.resolve_symbol("")
    assert "found 0 candidates" in str(ei.value)


def test_resolve_symbol_discovery_gold_exact(fake_mt5):
    fake_mt5.symbols = ["EURUSD", "GOLD", "GBPUSD"]
    a = Mt5ExecutionAdapter(_settings(symbol=""))
    a.connect()
    assert a.resolve_symbol("") == "GOLD"


def test_discover_gold_candidates_deterministic():
    a = Mt5ExecutionAdapter(_settings())
    got = a._discover_gold_candidates(["XAUUSDc", "XAUUSD", "XAUUSDm", "GOLD", "EURUSD"])
    # sorted, deduped, case-insensitive
    assert got == ["GOLD", "XAUUSD", "XAUUSDc", "XAUUSDm"]


# --------------------------------------------------------------------------
# Mode verification — configured vs actual MT5 account mode
# --------------------------------------------------------------------------


def test_connect_demo_configured_demo_account_ok(fake_mt5):
    fake_mt5.account.trade_mode = 0  # DEMO
    a = Mt5ExecutionAdapter(_settings(mode="demo"))
    a.connect()  # must not raise


def test_connect_demo_configured_real_account_raises(fake_mt5):
    fake_mt5.account.trade_mode = 2  # REAL
    a = Mt5ExecutionAdapter(_settings(mode="demo"))
    with pytest.raises(ModeGuardError):
        a.connect()


def test_connect_paper_configured_any_account_ok(fake_mt5):
    fake_mt5.account.trade_mode = 2  # REAL — paper is read-only anyway
    a = Mt5ExecutionAdapter(_settings(mode="paper"))
    a.connect()  # must not raise


def test_connect_live_configured_always_raises(fake_mt5):
    fake_mt5.account.trade_mode = 2  # REAL
    # constructor already guards: MT5_MODE=live is never auto-approved
    with pytest.raises(ModeGuardError):
        Mt5ExecutionAdapter(_settings(mode="live"))


def test_execution_environment_demo_demo_allowed():
    env = ExecutionEnvironment(configured_mode="demo", actual_account_mode=AccountMode.DEMO)
    env.allow_execution()  # must not raise


def test_execution_environment_demo_real_blocked():
    env = ExecutionEnvironment(configured_mode="demo", actual_account_mode=AccountMode.REAL)
    with pytest.raises(ModeGuardError):
        env.allow_execution()


def test_execution_environment_live_any_blocked():
    for actual in (AccountMode.DEMO, AccountMode.REAL, AccountMode.CONTEST):
        env = ExecutionEnvironment(configured_mode="live", actual_account_mode=actual)
        with pytest.raises(ModeGuardError):
            env.allow_execution()


# --------------------------------------------------------------------------
# SymbolInfo volume validation
# --------------------------------------------------------------------------


def _sym(**kw):
    base = dict(
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
    base.update(kw)
    return SymbolInfo(**base)  # type: ignore[arg-type]


def test_volume_normalize_normal():
    assert _sym().normalize_volume(0.05) == 0.05


def test_volume_normalize_below_min():
    assert _sym().normalize_volume(0.001) == 0.01


def test_volume_normalize_above_max():
    assert _sym().normalize_volume(999.0) == 100.0


def test_volume_normalize_exact_step():
    assert _sym().normalize_volume(0.03) == 0.03


def test_volume_normalize_fractional_step():
    # 0.015 -> floor to 0.01 grid
    assert _sym().normalize_volume(0.015) == 0.01


def test_volume_invalid_step_zero_raises():
    with pytest.raises(ValueError):
        _sym(volume_step=0)


def test_volume_invalid_min_max_raises():
    with pytest.raises(ValueError):
        _sym(volume_min=0)
    with pytest.raises(ValueError):
        _sym(volume_min=5.0, volume_max=1.0)
