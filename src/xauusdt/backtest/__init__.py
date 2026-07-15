"""Backtest module for XAUUSDT perpetual strategy simulation.

Uses stored OKX candlestick data to simulate trading strategies.
No live exchange access, no API keys required.

Design:
    stored candles
        ↓
    strategy.on_candle(candle, position, context) → Signal
        ↓
    engine executes signal → updates position
        ↓
    metrics computed from trade history
"""

from __future__ import annotations
