# Confluence v1 Sensitivity & Ablation Analysis (PROJECT-BACKTEST-005)

**Date**: 2026-07-16
**Data**: XAU-USDT-SWAP 15m | 2026-04-16 to 2026-07-14 (8,640 candles)
**Status**: Exploratory analysis (not optimization)

## Baseline (BACKTEST-004, Post-EMA-fix)

| Metric | Value |
|---|---|
| Trades | 250 |
| Win Rate | 36.0% |
| Net PnL | -211.32 |
| Max DD | 21.3% |
| Expectancy | -0.85 |

## Sensitivity Results

| Variant | Trades | Win Rate | Net PnL | Max DD | Expectancy |
|---|---|---|---|---|---|
| Baseline (65/15/20) | 250 | 36.0% | -211.32 | 21.3% | -0.85 |
| min_score=55 | 324 | 38.3% | -257.69 | - | -0.80 |
| min_score=60 | 255 | 35.7% | -215.58 | - | -0.85 |
| min_score=70 | 241 | 36.1% | -204.49 | - | -0.85 |
| gap=5 | 309 | 40.8% | -242.70 | - | -0.79 |
| gap=10 | 250 | 36.0% | -211.05 | - | -0.85 |
| adx_min=15 | 271 | 36.9% | -209.85 | - | -0.77 |
| adx_min=25 | 219 | 35.2% | -197.78 | - | -0.90 |
| swing_lookback=20 | 287 | 36.6% | -236.23 | - | -0.82 |
| swing_lookback=30 | 288 | 36.5% | -236.00 | - | -0.82 |

## Ablation Results

| Variant | Trades | Win Rate | Net PnL | Max DD | Expectancy |
|---|---|---|---|---|---|
| SWING DISABLED | 95 | 32.6% | -94.38 | - | -0.99 |
| STRUCTURE DISABLED | 44 | 34.1% | -48.38 | - | -1.10 |
| ATR DISABLED | 95 | 31.6% | -102.00 | - | -1.07 |

## Key Findings

1. **EMA formula bug was the single largest issue** — Before fixing `calc_ema` (which incorrectly multiplied `prev * multiplier`), the strategy generated exactly 1 trade in 90 days because all EMA values were scaled by ~4% (EMA 50→188 instead of ~4800).
2. **Trade frequency is now reasonable** — Baseline produces 250 trades (~2.7/day), so the strategy doesn't need to be made more sensitive.
3. **Win rate is the real problem** — 36% win rate with 2x RR should be profitable, but fees (0.05%) and slippage (2 bps) on XAUUSDT eat the edge.
4. **Swing context is critical** — Disabling swing drops trades from 250 to 95, meaning ~62% of baseline trades relied on a recent swing point to pass the 65 threshold. Swing is doing its job correctly.
5. **Structure is the heaviest component** — Disabling structure drops trades to just 44. This is expected since structure is worth 20 points, but it also means many entries rely on the structure component alone.

## Recommended Strategy v2 Changes

- **Do NOT lower min_score** — This increases trade volume but degrades net PnL.
- **Do NOT disable structure** — Structure is a core component that drives entry quality.
- **Focus on improving win rate**: Tighten ADX threshold to 25 (drops trades to 219, win rate 35.2%, but best PnL of all variants at -197.78), or add a secondary confirmation filter to exclude false entries.
- **Consider raising Risk/Reward to 2.5x** — The 2.0x RR is not enough to overcome the 36% win rate with 0.05% fees + 2bps slippage.

## Recommended Next Steps

1. **PROJECT-STRATEGY-002**: Implement Strategy v2 with ADX 25 threshold and RR 2.5x (or similar), keeping `min_score=65` and structure/swing intact.
2. **PROJECT-BACKTEST-006**: Re-run baseline with Strategy v2 config to validate improvements.
