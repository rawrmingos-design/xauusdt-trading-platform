# Data Quality Report — PROJECT-DATA-009B
**Generated**: 2026-07-15T18:34:39.961251+00:00
**Canonical Symbol**: XAU-USDT-SWAP

## Backfill Commands
```bash
uv run python tools/run_backfill.py --symbol XAU-USDT-SWAP --granularity 15m --days 90
uv run python tools/run_backfill.py --symbol XAU-USDT-SWAP --granularity 1H --days 90
```

## Results Summary

| Metric | Value |
|--------|-------|
### 15m Candles
| Property | Value |
|----------|-------|
| Total stored | 8640 |
| Expected count | 8640 |
| Coverage | 100.0% |
| Gaps | 0 |
| Duplicates | 0 |
| Earliest | 2026-04-16T00:00:00+00:00 |
| Latest | 2026-07-14T23:45:00+00:00 |

### 1H Candles
| Property | Value |
|----------|-------|
| Total stored | 2160 |
| Expected count | 2160 |
| Coverage | 100.0% |
| Gaps | 0 |
| Duplicates | 0 |
| Earliest | 2026-04-16T00:00:00+00:00 |
| Latest | 2026-07-14T23:00:00+00:00 |

## REST-vs-DB Comparison

- **100 OKX vs 97 DB**: pass (mismatches: 0)
- **100 OKX vs 97 DB**: pass (mismatches: 0)
- **100 OKX vs 97 DB**: pass (mismatches: 0)

## Known Limitations

- OKX public API limited to 100 candles per request.
- Public API may not have full history before XAU-USDT-SWAP was listed on OKX.
- No live candle validation — only historical REST comparison.
- Price comparison tolerance is $0.01.

## Recommended Next Steps

- PROJECT-BACKTEST-004: Rerun baseline backtest with 90-day data and EMA 50/200 config.
- Add 1D granularity if needed for multi-timeframe analysis.
- Implement automated candle freshness monitoring.