"""Tests for improved exit model (PROJECT-STRATEGY-003)."""

from datetime import UTC, datetime
from unittest import TestCase, main

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig, BacktestPosition, BacktestTrade, Side
from xauusdt.exchange.models import Candle
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy


def make_candle(open_time, open_p, high, low, close, volume=100.0):
    """Helper to create a simple 15m candle."""
    return Candle(
        symbol="XAU-USDT-SWAP",
        granularity="15m",
        open_time=open_time,
        open=open_p,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class TestBacktestPositionPartialTP(TestCase):
    """Test BacktestPosition for partial TP and break-even logic."""

    def setUp(self):
        self.base_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        self.entry_price = 100.0
        self.stop_loss_price = 95.0
        self.take_profit_price = 110.0
        self.partial_tp_price = 105.0  # 1.0R (100 + 5)
        self.position = BacktestPosition(
            side=Side.LONG,
            entry_candle_time=self.base_time.isoformat(),
            entry_price=self.entry_price,
            quantity=10.0,
            stop_loss_price=self.stop_loss_price,
            take_profit_price=self.take_profit_price,
            partial_tp_price=self.partial_tp_price,
        )

    def test_partial_tp_not_hit_before_1r(self):
        """Partial TP should NOT be triggered at 104."""
        candle = make_candle(self.base_time, 100, 104, 99, 103, 100)
        self.assertFalse(self.position.is_partial_tp_hit(candle))

    def test_partial_tp_hit_at_1r(self):
        """Partial TP should be triggered at 105."""
        candle = make_candle(self.base_time, 100, 105, 99, 104, 100)
        self.assertTrue(self.position.is_partial_tp_hit(candle))

    def test_partial_tp_not_hit_after_close(self):
        """Partial TP should NOT be triggered after partial close."""
        candle_hit = make_candle(self.base_time, 100, 105, 99, 104, 100)
        self.assertTrue(self.position.is_partial_tp_hit(candle_hit))
        self.position.is_partial_closed = True
        candle_later = make_candle(self.base_time, 105, 107, 104, 106, 100)
        self.assertFalse(self.position.is_partial_tp_hit(candle_later))

    def test_break_even_sl_hit(self):
        """Break-even SL should trigger when candle low <= entry price."""
        self.position.is_partial_closed = True
        self.position.stop_loss_price = self.position.entry_price
        candle = make_candle(self.base_time, 104, 104, 99, 99.9, 100)
        self.assertTrue(self.position.is_sl_hit(candle))

    def test_short_break_even_sl_hit(self):
        """Break-even SL for SHORT should trigger when candle high >= entry price."""
        self.position.side = Side.SHORT
        self.position.is_partial_closed = True
        self.position.stop_loss_price = self.position.entry_price
        candle = make_candle(self.base_time, 100, 100.5, 99, 99.5, 100)
        self.assertTrue(self.position.is_sl_hit(candle))


class TestBacktestEnginePartialTP(TestCase):
    """Test ConfluenceBacktestEngine for partial TP handling."""

    def test_partial_close_records_trade(self):
        """A partial TP close should record a trade with is_partial=True."""
        base_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)

        # Create candles that guarantee price goes from 100 -> 104 -> 105 -> 103
        candles = []
        candles.append(make_candle(base_time, 100, 100, 100, 100))
        candles.append(make_candle(base_time.replace(minute=1), 100, 104, 100, 103))
        candles.append(make_candle(base_time.replace(minute=2), 103, 105, 103, 104))  # Hits partial TP
        candles.append(make_candle(base_time.replace(minute=3), 104, 105, 103, 103))

        bt_config = BacktestConfig(initial_balance=10000, fee_rate=0.0005, slippage_bps=2.0)

        # Use a dummy ConfluenceStrategy just to satisfy engine interface
        dummy_strategy = ConfluenceStrategy(ConfluenceConfig(
            version="test_partial", improved_exit=True, sl_atr_multiplier=2.0, risk_reward_ratio=2.0
        ))
        engine = ConfluenceBacktestEngine(bt_config, candles, dummy_strategy)

        # Simulate opening a position manually at index 0
        engine._position = BacktestPosition(
            side=Side.LONG,
            entry_candle_time=candles[0].open_time.isoformat(),
            entry_price=100.0,
            quantity=10.0,
            stop_loss_price=95.0,  # 0.5R SL
            take_profit_price=110.0,  # 2.0R TP
            partial_tp_price=105.0,  # 1.0R Partial TP
        )
        engine._trades = []
        engine._balance = 10000.0

        # Run candles up to where partial TP is hit
        for i, candle in enumerate(candles[1:], start=1):
            engine._process_candle(candle, i)

        # Check that a partial trade was recorded
        partial_trades = [t for t in engine._trades if t.is_partial]
        self.assertTrue(len(partial_trades) > 0, "Partial TP trade should be recorded")
        self.assertTrue(partial_trades[0].is_partial)
        self.assertEqual(partial_trades[0].exit_reason, "PARTIAL_TP")
        self.assertAlmostEqual(partial_trades[0].quantity, 5.0, places=5)

    def test_remaining_position_closes_at_final_tp(self):
        """After partial TP, the remaining 50% should close at final TP or BE."""
        base_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)

        # Candles go up to 100 -> partial TP at 105 -> final TP at 110
        candles = []
        candles.append(make_candle(base_time, 100, 100, 100, 100))
        candles.append(make_candle(base_time.replace(minute=1), 100, 105, 100, 104))
        candles.append(make_candle(base_time.replace(minute=2), 104, 110, 104, 110))  # Hits final TP

        bt_config = BacktestConfig(initial_balance=10000, fee_rate=0.0005, slippage_bps=2.0)
        dummy_strategy = ConfluenceStrategy(ConfluenceConfig(version="test_final", improved_exit=True))
        engine = ConfluenceBacktestEngine(bt_config, candles, dummy_strategy)

        engine._position = BacktestPosition(
            side=Side.LONG,
            entry_candle_time=candles[0].open_time.isoformat(),
            entry_price=100.0,
            quantity=10.0,
            stop_loss_price=95.0,
            take_profit_price=110.0,
            partial_tp_price=105.0,
        )
        engine._trades = []
        engine._balance = 10000.0

        for i, candle in enumerate(candles[1:], start=1):
            engine._process_candle(candle, i)

        # Find the full close (final TP)
        full_trades = [t for t in engine._trades if not t.is_partial]
        self.assertTrue(len(full_trades) > 0, "Final position should close")
        self.assertAlmostEqual(full_trades[0].quantity, 5.0, places=5)
        self.assertAlmostEqual(full_trades[0].exit_price, 110.02, places=2)  # ~110 with slippage


class TestBreakEvenStop(TestCase):
    """Test break-even stop movement after partial close."""

    def test_long_break_even_sl(self):
        """Long position: after partial close, SL moves to entry price."""
        base_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        candles = []
        candles.append(make_candle(base_time, 100, 100, 100, 100))
        # Go up to 105 (partial TP), then slowly drift back down to 99 (BE SL)
        candles.append(make_candle(base_time.replace(minute=1), 100, 105, 100, 104))
        candles.append(make_candle(base_time.replace(minute=2), 104, 100, 99.98, 99.98))

        bt_config = BacktestConfig(initial_balance=10000, fee_rate=0.0005, slippage_bps=2.0)
        dummy_strategy = ConfluenceStrategy(ConfluenceConfig(version="test_be", improved_exit=True))
        engine = ConfluenceBacktestEngine(bt_config, candles, dummy_strategy)

        engine._position = BacktestPosition(
            side=Side.LONG,
            entry_candle_time=candles[0].open_time.isoformat(),
            entry_price=100.0,
            quantity=10.0,
            stop_loss_price=95.0,
            take_profit_price=110.0,
            partial_tp_price=105.0,
        )
        engine._trades = []
        engine._balance = 10000.0

        for i, candle in enumerate(candles[1:], start=1):
            engine._process_candle(candle, i)

        trades = engine._trades
        # Should have a PARTIAL_TP trade and an SL trade
        partial_trades = [t for t in trades if t.is_partial]
        sl_trades = [t for t in trades if t.exit_reason == "SL"]
        self.assertTrue(len(partial_trades) > 0)
        self.assertTrue(len(sl_trades) > 0)
        self.assertAlmostEqual(sl_trades[0].exit_price, 99.96, delta=0.05)


class TestBacktestTradePartial(TestCase):
    """Test BacktestTrade with is_partial flag."""

    def test_partial_trade_serialization(self):
        """BacktestTrade should correctly handle is_partial in __dict__."""
        trade = BacktestTrade(
            entry_candle_time="2024-01-01T00:00:00+00:00",
            entry_price=100.0,
            exit_candle_time="2024-01-01T01:00:00+00:00",
            exit_price=105.0,
            side="LONG",
            quantity=5.0,
            pnl=25.0,
            pnl_pct=5.0,
            fee=0.5,
            slippage_cost=0.1,
            exit_reason="PARTIAL_TP",
            is_partial=True,
        )
        self.assertTrue(trade.is_partial)
        self.assertEqual(trade.exit_reason, "PARTIAL_TP")
        self.assertEqual(trade.quantity, 5.0)


if __name__ == "__main__":
    main()
