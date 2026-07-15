"""Technical feature engine for XAUUSDT candles.

Deterministic, no-lookahead feature calculations:
- EMA (Exponential Moving Average)
- ATR (Average True Range)
- ADX (Average Directional Index)
- Swing high/low detection (confirmed + candidate)
- Market structure classification

All functions accept plain Python data — no pandas, no live API.
"""

from __future__ import annotations
