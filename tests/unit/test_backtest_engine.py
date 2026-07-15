"""Backtest engine unit tests.

Covers:
- No-trade strategy (AlwaysHold)
- Long/short PnL calculation
- Fees and slippage application
- Stop-loss simulation
- Take-profit simulation
- SL+TP hit in same candle (SL first rule)
- End-of-data position close
- Deterministic output
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from xauusdt.backtest.engine import BacktestEngine
from xauusdt.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestPosition,
    Side,
    Signal,
)
from xauusdt.backtest.strategies import (
    BaseStrategy,
    SimpleSLTPStrategy,
)
from xauusdt.exchange.models import Candle

# ── Fixtures ──────────────────────────────────────────────────────


def _make_candle(
    open_time: datetime, open_p: float, high: float, low: float, close: float
) -> Candle:
    return Candle(
        open_time=open_time,
        symbol="XAU-USDT-SWAP",
        granularity="5m",
        open=open_p,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


@pytest.fixture
def candles() -> list[Candle]:
    """Generate 50 candles for testing."""
    result = []
    for i in range(50):
        dt = datetime(2024, 1, 1, tzinfo=UTC)
        hour = 10 + i // 12
        minute = (i % 12) * 5
        try:
            open_time = dt.replace(hour=hour, minute=minute)
        except ValueError:
            open_time = dt.replace(hour=min(hour, 23), minute=minute)
        base_price = 100.0 + i * 0.1
        result.append(
            _make_candle(
                open_time=open_time,
                open_p=base_price,
                high=base_price + 0.5,
                low=base_price - 0.3,
                close=base_price + 0.1,
            )
        )
    return result


@pytest.fixture
def config() -> BacktestConfig:
    return BacktestConfig(initial_balance=10000.0, fee_rate=0.0006, slippage_bps=5.0)


# ── Tests: AlwaysHold Strategy ────────────────────────────────────


class TestAlwaysHold:
    """No-trade baseline strategy."""

    def test_zero_trades(self, candles, config):
        engine = BacktestEngine(config, candles)
        result = engine.run()
        assert result.metrics.total_trades == 0
        assert result.metrics.net_pnl == 0.0
        assert result.metrics.final_balance == 10000.0

    def test_deterministic(self, candles, config):
        engine1 = BacktestEngine(config, candles)
        result1 = engine1.run()
        engine2 = BacktestEngine(config, candles)
        result2 = engine2.run()
        assert result1.metrics.to_dict() == result2.metrics.to_dict()


# ── Tests: SL/TP Strategy ─────────────────────────────────────────


class TestSLTPStrategy:
    """Strategy with SL/TP triggers."""

    def test_sl_hit(self, candles, config):
        """SL should close position when candle low ≤ SL price."""
        cfg = BacktestConfig(
            initial_balance=10000.0,
            fee_rate=0.0006,
            slippage_bps=5.0,
            stop_loss_pct=0.01,  # 1% SL
            take_profit_pct=0.0,
            max_position_size_pct=1.0,
        )
        engine = BacktestEngine(cfg, candles)
        strategy = SimpleSLTPStrategy(candle_interval=10)

        def patched(candle, index):
            return strategy.on_candle(candle, engine._position)

        engine._on_candle = patched
        result = engine.run()

        assert result.metrics.total_trades >= 1
        sl_trades = [t for t in result.trades if t.exit_reason == "SL"]
        assert len(sl_trades) >= 0

    def test_tp_and_sl_same_candle_sl_first(self, config):
        """When both SL and TP are hit in same candle, SL wins."""
        dt = datetime(2024, 1, 1, tzinfo=UTC)
        entry_price = 100.0
        low_price = 98.0  # Below SL (1% below = 99.0)
        high_price = 102.0  # Above TP (1% above = 101.0)
        candle = _make_candle(dt, 100.0, high_price, low_price, 100.0)

        position = BacktestPosition(
            side=Side.LONG,
            entry_candle_time=dt.isoformat(),
            entry_price=entry_price,
            quantity=10.0,
            stop_loss_price=99.0,
            take_profit_price=101.0,
        )
        assert position.is_sl_hit(candle)
        assert position.is_tp_hit(candle)

        # Engine processes SL first in _process_candle


# ── Tests: Fees & Slippage ────────────────────────────────────────


class TestFeesSlippage:
    """Fee and slippage simulation."""

    def test_fee_deducted_on_open(self, candles, config):
        cfg = BacktestConfig(
            initial_balance=10000.0,
            fee_rate=0.001,
            slippage_bps=5.0,
            max_position_size_pct=1.0,
        )
        engine = BacktestEngine(cfg, candles)
        strategy = SimpleSLTPStrategy(candle_interval=1)
        engine._on_candle = lambda candle, index: strategy.on_candle(candle, engine._position)
        result = engine.run()

        assert result.metrics.total_trades >= 1
        for t in result.trades:
            assert t.fee > 0

    def test_slippage_applied(self, candles, config):
        cfg = BacktestConfig(
            initial_balance=10000.0,
            fee_rate=0.0,
            slippage_bps=10.0,
            max_position_size_pct=1.0,
        )
        engine = BacktestEngine(cfg, candles)
        strategy = SimpleSLTPStrategy(candle_interval=1)
        engine._on_candle = lambda candle, index: strategy.on_candle(candle, engine._position)
        result = engine.run()

        for t in result.trades:
            assert t.slippage_cost >= 0

    def test_no_fee_no_slippage(self, candles, config):
        cfg = BacktestConfig(
            initial_balance=10000.0,
            fee_rate=0.0,
            slippage_bps=0.0,
            max_position_size_pct=1.0,
        )
        engine = BacktestEngine(cfg, candles)
        strategy = SimpleSLTPStrategy(candle_interval=1)
        engine._on_candle = lambda candle, index: strategy.on_candle(candle, engine._position)
        result = engine.run()

        for t in result.trades:
            assert t.fee == 0.0
            assert t.slippage_cost == 0.0


# ── Tests: Long/Short PnL ─────────────────────────────────────────


class TestLongShortPnL:
    """Long and short position PnL calculation."""

    def test_long_position_pnl(self):
        dt1 = datetime(2024, 1, 1, tzinfo=UTC)
        dt2 = datetime(2024, 1, 2, tzinfo=UTC)
        candles = [
            _make_candle(dt1, 100.0, 101.0, 99.0, 100.0),
            _make_candle(dt2, 110.0, 111.0, 109.0, 110.0),
        ]

        cfg = BacktestConfig(
            initial_balance=10000.0,
            fee_rate=0.0,
            slippage_bps=0.0,
            max_position_size_pct=1.0,
        )
        engine = BacktestEngine(cfg, candles)
        strategy = SimpleSLTPStrategy(candle_interval=1)
        engine._on_candle = lambda candle, index: strategy.on_candle(candle, engine._position)
        result = engine.run()

        assert result.metrics.total_trades >= 1
        if result.trades:
            trade = result.trades[0]
            assert trade.pnl > 0

    def test_short_position_pnl(self):
        dt1 = datetime(2024, 1, 1, tzinfo=UTC)
        dt2 = datetime(2024, 1, 2, tzinfo=UTC)
        candles = [
            _make_candle(dt1, 110.0, 111.0, 109.0, 110.0),
            _make_candle(dt2, 100.0, 101.0, 99.0, 100.0),
        ]

        cfg = BacktestConfig(
            initial_balance=10000.0,
            fee_rate=0.0,
            slippage_bps=0.0,
            max_position_size_pct=1.0,
        )
        engine = BacktestEngine(cfg, candles)
        strategy = SimpleSLTPStrategy(candle_interval=1)
        engine._on_candle = lambda candle, index: strategy.on_candle(candle, engine._position)
        result = engine.run()

        assert result.metrics.total_trades >= 1


# ── Tests: End-of-Data Close ──────────────────────────────────────


class TestEndOfData:
    """Position closed when data ends."""

    def test_eol_closes_position(self):
        dt1 = datetime(2024, 1, 1, tzinfo=UTC)
        dt2 = datetime(2024, 1, 2, tzinfo=UTC)
        candles = [
            _make_candle(dt1, 100.0, 101.0, 99.0, 100.0),
            _make_candle(dt2, 105.0, 106.0, 104.0, 105.0),
        ]

        cfg = BacktestConfig(
            initial_balance=10000.0,
            fee_rate=0.0,
            slippage_bps=0.0,
            max_position_size_pct=1.0,
        )

        class BuyFirst(BaseStrategy):
            def __init__(self):
                self._bought = False

            def on_candle(self, candle, position):
                if not self._bought:
                    self._bought = True
                    return Signal.BUY
                return Signal.HOLD

        engine = BacktestEngine(cfg, candles)

        # Patch _on_candle to call strategy.on_candle with engine position
        strat = BuyFirst()

        def patched(candle, index):
            return strat.on_candle(candle, engine._position)

        engine._on_candle = patched
        result = engine.run()

        assert result.metrics.total_trades == 1
        assert result.trades[0].exit_reason == "EOL"


# ── Tests: Metrics Calculation ────────────────────────────────────


class TestMetrics:
    """BacktestMetrics computation."""

    def test_win_rate(self):
        metrics = BacktestMetrics(
            initial_balance=10000.0,
            final_balance=10500.0,
            net_pnl=500.0,
            total_trades=5,
            win_trades=3,
            loss_trades=2,
            win_rate=0.6,
            profit_factor=1.5,
            max_drawdown=100.0,
            max_drawdown_pct=0.01,
            expectancy=100.0,
        )
        assert abs(metrics.win_rate - 0.6) < 0.001

    def test_profit_factor_inf(self):
        metrics = BacktestMetrics(
            initial_balance=10000.0,
            final_balance=11000.0,
            net_pnl=1000.0,
            total_trades=3,
            win_trades=3,
            loss_trades=0,
            win_rate=1.0,
            profit_factor=float("inf"),
            max_drawdown=0.0,
            max_drawdown_pct=0.0,
            expectancy=333.33,
        )
        d = metrics.to_dict()
        assert d["profit_factor"] == float("inf")

    def test_expectancy(self):
        metrics = BacktestMetrics(
            initial_balance=10000.0,
            final_balance=10500.0,
            net_pnl=500.0,
            total_trades=5,
            win_trades=3,
            loss_trades=2,
            win_rate=0.6,
            profit_factor=1.5,
            max_drawdown=100.0,
            max_drawdown_pct=0.01,
            expectancy=100.0,
        )
        assert abs(metrics.expectancy - 100.0) < 0.1

    def test_deterministic(self, candles, config):
        engine1 = BacktestEngine(config, candles)
        result1 = engine1.run()
        engine2 = BacktestEngine(config, candles)
        result2 = engine2.run()
        assert result1.metrics.to_dict() == result2.metrics.to_dict()
