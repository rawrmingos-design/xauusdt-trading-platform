# Confluence V1 vs V2 Comparison (PROJECT-BACKTEST-006)

**Date**: 2026-07-16 03:43 UTC
**Dataset**: XAU-USDT-SWAP 15m (8640 candles, 90 days)

## Performance Metrics

| Metric | V1 (Baseline) | V2 (Quality Filters) | Diff |
|---|---|---|---|
| Total Trades | 705 | 527 | -178 |
| Win Rate | 34.9% | 28.7% | -6.2% |
| Net PnL | -473.68 | -437.20 | +36.48 |
| Profit Factor | 1.05 | 0.92 | -0.13 |
| Max Drawdown | 47.4% | 44.4% | -3.0% |
| Expectancy | -0.67 | -0.83 | -0.16 |

## Trade Analysis

| Metric | V1 | V2 |
|---|---|---|
| Longs / Shorts | 336 / 369 | 256 / 271 |
| SL Hits | 340 | 324 |
| TP Hits | 154 | 97 |
| Signal Closes | 210 | 106 |
| EOL Closes | 1 | 0 |
| Avg Win | 2.84 | 3.06 |
| Avg Loss | -1.45 | -1.33 |
| Avg Duration | 2.5h | 2.1h |

## Rejection Analysis

V2 DID NOT significantly improve trade quality relative to its own schedule. It rejected 610 trades V1 would have taken (399 losses, 211 wins). The rejected trades had a WR of 34.6%.

## Conclusion & Next Steps

### Limitations
- Single 90-day period on one symbol.
- Fixed risk/reward and trailing logic not tested.

### Recommended Next Steps
- **PROJECT-BACKTEST-007**: V2 still has negative expectancy. We must analyze the Exit Model (SL/TP) via MFE/MAE analysis. The entry logic might be fine, but the exit rules are burning capital to slippage/fees.
