# Bitget Candle Collector Runbook

## Overview
Collects XAUUSDT perpetual futures candles from Bitget without gaps.

## Start
```bash
python -m src.collectors.bitget_candles
```

## Configuration
- Symbol: XAUUSDT
- Timeframe: 1m
- Exchange: Bitget

## Monitoring
- Check logs for gap detection alerts
- Monitor WebSocket connection health
- Verify backfill completion
