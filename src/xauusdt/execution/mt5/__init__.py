"""MT5 execution venue adapter (PROJECT-MT5-001).

Only this package may import ``MetaTrader5`` — and only :mod:`client` does
(and lazily, so the rest of the platform never needs a terminal).
"""
