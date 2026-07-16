# Baseline Confluence Backtest Report (PROJECT-BACKTEST-004)

**Date**: 2026-07-16
**Objective**: Baseline run of Confluence v1 using 90-day canonical XAU-USDT-SWAP data and explicitly configured EMA 50/200 config.

## Data

- **Symbol**: XAU-USDT-SWAP (Canonical OKX)
- **Granularity**: 15m
- **Range**: 2026-04-16 to 2026-07-14 (90 Days)
- **Total Candles**: 8,640
- **Warmup Candles**: 210 (EMA 200 + 10 padding)
- **Usable Candles**: 8,430

## Configuration

- Initial balance: 1000
- Fee rate: 0.05%
- Slippage: 2.0 bps
- Min score: 65.0
- Min score gap: 15.0
- EMA: 50 / 200
- ADX threshold: 20.0
- ATR SL multiplier: 1.5
- Risk/Reward: 2.0

## Results Overview

| Metric | Value |
|---|---|
| Net PnL | -211.32 |
| Final Balance | 788.68 |
| Total Trades | 250 |
| Win Rate | 36.0% (90 W / 160 L) |
| Profit Factor | 1.02 |
| Max Drawdown | 213.15 (21.3%) |
| Expectancy | -0.85 |

## Score Distribution & Analysis

After fixing the EMA calculation formula bug in `FeatureEngine` (which previously forced all trades to be interpreted as bullish trend), the strategy generates a healthy 250 trades over 90 days.
The win rate is 36.0% with a profit factor of 1.02. This means winning trades are slightly larger than losing trades on average, but the win rate is too low to be profitable given the 1.5 ATR stop loss and 2.0 risk-reward ratio.

## Recommended Next Steps

1. **PROJECT-BACKTEST-005 (Ablation Analysis)**: We will run sensitivity and ablation analysis on min_score, ADX, and swing lookback to see which components are negatively impacting win rate.
2. **PROJECT-STRATEGY-002 (Refine Confluence Scoring)**: Refine scoring and trade logic to improve the win rate and lower max drawdown.
