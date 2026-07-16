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
| Net PnL | -158.82 |
| Final Balance | 841.18 |
| Total Trades | 1 |
| Win Rate | 0.0% (0 W / 1 L) |
| Profit Factor | 0.00 |
| Max Drawdown | 177.53 (17.8%) |
| Expectancy | -158.82 |

## Comparison to Previous Baseline (EMA 9/21, 7 Days)

- **Previous Baseline (BACKTEST-003)**: EMA 9/21 config over 7 days generated **11 trades** with a 63.6% win rate (but negative net PnL).
- **Current Baseline (BACKTEST-004)**: EMA 50/200 config over 90 days generated only **1 trade** with a 0.0% win rate.
- **Why?**: The combination of a slower trend filter (EMA 50/200 crossover) and strict confluence scoring (requiring EMA, Price, Structure, Swing, ADX, and ATR to align simultaneously) severely limits entry opportunities on the 15m timeframe. 

## Score Distribution & Rejection Analysis

From diagnostic tooling (`docs/reports/diagnostic_BACKTEST-004.json`):
- **Component Passes (out of 8430 candles)**:
  - EMA Trend: 8431 (100% of usable candles)
  - Price vs EMA: 8431 (100%)
  - Structure: 8431 (100%)
  - ADX Strong: 10872
  - ATR Normal: 16862
- **Near-Misses (Score >= 45 but no signal)**: 1,210 candles.
- **Top Rejection Reasons**:
  - `low_score_50_64`: 1,114
  - `low_score_below_60`: 823
  - `insufficient_gap`: 643
- **Primary Blockers**: The primary reason near-misses failed to trigger a trade was a mismatch between the Market Structure bias and the EMA bias, combined with the lack of an immediate Swing Point support/resistance.

## Known Limitations

1. **Trade Frequency**: 1 trade in 90 days is insufficient for statistical significance.
2. **Strategy Strictness**: Confluence v1 requires up to 6 different indicators to align in a single 15m candle. EMA 50/200 introduces significant lag, meaning by the time EMA is aligned, Structure or ADX might already be exhausted.
3. **No Optimization**: Parameters (weights, minimum score 65) were hardcoded based on initial assumptions, not optimized.

## Recommended Next Steps

1. **PROJECT-STRATEGY-002 (Refine Confluence Scoring)**: Lower `min_score` to 55-60, or adjust the weights for `EMA` and `Structure` to prevent them from blocking each other as often. Alternatively, consider making the ADX/ATR conditions multiplicative instead of additive.
2. **PROJECT-BACKTEST-005 (Failure Mode Analysis)**: Analyze near-misses (e.g., candles with score = 60) to determine if they would have been profitable trades if the threshold was lower.

## Reproducible Command

```bash
uv run python tools/run_baseline_backtest.py
```
