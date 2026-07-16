# Exit Model Analysis (PROJECT-BACKTEST-007)

**Date**: 2026-07-16 04:34 UTC
**Dataset**: XAU-USDT-SWAP 15m (90 days)

## SUPERSEDED NOTICE

Pre-PR #25 backtest reports (including BACKTEST-004 and BACKTEST-006 baseline) **do not** reflect real exit behavior because ATR-based SL/TP were not active. All exit analysis here uses the corrected engine from PR #25.

## V1 Baseline Comparison (ATR Stop vs RR)

| Exit Config | Trades | Win Rate | SL% | TP% | Signal% | Avg MFE | Net PnL | Max MFE | Avg MAE | Stopped <1R | Reached 1R | Reached 2R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RR=1.0/ATR=1.0 | 1871 | 41.5% | 54.3% | 35.4% | 10.3% | 0.74R | -28.52 | 9.51R | 1.07R | 41.4% | 0.1% | 0.0% |
| RR=1.0/ATR=1.5 | 1321 | 41.0% | 42.2% | 35.3% | 22.5% | 0.71R | 28.08 | 8.51R | 0.90R | 41.0% | 0.0% | 0.0% |
| RR=1.0/ATR=2.0 | 1059 | 40.5% | 30.7% | 34.3% | 35.0% | 0.64R | 58.63 | 6.83R | 0.74R | 40.5% | 0.0% | 0.0% |
| RR=1.0/ATR=2.5 | 893 | 38.9% | 24.5% | 30.7% | 44.8% | 0.59R | 55.10 | 5.46R | 0.67R | 38.9% | 0.0% | 0.0% |
| RR=1.5/ATR=1.0 | 1638 | 36.1% | 59.2% | 26.9% | 13.9% | 0.87R | -17.57 | 11.80R | 1.14R | 36.1% | 0.1% | 0.0% |
| RR=1.5/ATR=1.5 | 1123 | 36.9% | 44.4% | 27.3% | 28.2% | 0.84R | 60.89 | 8.66R | 0.93R | 36.9% | 0.0% | 0.0% |
| RR=1.5/ATR=2.0 | 872 | 34.9% | 31.4% | 24.0% | 44.6% | 0.76R | 54.90 | 6.79R | 0.78R | 34.9% | 0.0% | 0.0% |
| RR=1.5/ATR=2.5 | 761 | 34.8% | 24.3% | 20.6% | 55.1% | 0.69R | 51.97 | 6.70R | 0.67R | 34.8% | 0.0% | 0.0% |
| RR=2.0/ATR=1.0 | 1462 | 34.3% | 60.7% | 23.0% | 16.2% | 1.00R | 32.74 | 13.08R | 1.17R | 34.1% | 0.2% | 0.0% |
| RR=2.0/ATR=1.5 | 1012 | 32.3% | 45.6% | 20.2% | 34.3% | 0.90R | 40.87 | 9.08R | 0.96R | 32.3% | 0.0% | 0.0% |
| RR=2.0/ATR=2.0 | 801 | 34.0% | 30.5% | 19.1% | 50.4% | 0.83R | 65.54 | 8.38R | 0.79R | 33.8% | 0.1% | 0.0% |
| RR=2.0/ATR=2.5 | 701 | 32.7% | 23.4% | 14.4% | 62.2% | 0.73R | 47.67 | 5.40R | 0.67R | 32.7% | 0.0% | 0.0% |
| RR=2.5/ATR=1.0 | 1346 | 30.2% | 63.4% | 17.9% | 18.6% | 1.02R | -52.48 | 13.08R | 1.21R | 30.1% | 0.1% | 0.0% |
| RR=2.5/ATR=1.5 | 925 | 32.3% | 45.4% | 17.4% | 37.2% | 0.98R | 59.52 | 9.04R | 0.97R | 32.2% | 0.1% | 0.0% |
| RR=2.5/ATR=2.0 | 741 | 31.3% | 31.4% | 13.8% | 54.8% | 0.87R | 43.37 | 7.06R | 0.80R | 31.3% | 0.0% | 0.0% |
| RR=2.5/ATR=2.5 | 662 | 31.6% | 22.5% | 11.8% | 65.7% | 0.78R | 37.59 | 6.69R | 0.68R | 31.6% | 0.0% | 0.0% |

## V2 Candidate Comparison (ATR Stop vs RR)

| Exit Config | Trades | Win Rate | SL% | TP% | Signal% | Avg MFE | Net PnL | Max MFE | Avg MAE | Stopped <1R | Reached 1R | Reached 2R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RR=1.0/ATR=1.0 | 1004 | 43.2% | 60.0% | 38.3% | 1.7% | 0.83R | 33.98 | 12.28R | 1.15R | 43.0% | 0.2% | 0.0% |
| RR=1.0/ATR=1.5 | 738 | 40.7% | 54.9% | 37.7% | 7.5% | 0.75R | -21.24 | 7.75R | 1.00R | 40.7% | 0.0% | 0.0% |
| RR=1.0/ATR=2.0 | 619 | 39.7% | 42.6% | 37.8% | 19.5% | 0.70R | -39.11 | 6.45R | 0.86R | 39.7% | 0.0% | 0.0% |
| RR=1.0/ATR=2.5 | 521 | 39.9% | 33.8% | 36.1% | 30.1% | 0.67R | 26.43 | 5.16R | 0.76R | 39.9% | 0.0% | 0.0% |
| RR=1.5/ATR=1.0 | 874 | 37.1% | 67.0% | 29.9% | 3.1% | 0.95R | 7.27 | 11.64R | 1.23R | 36.7% | 0.3% | 0.0% |
| RR=1.5/ATR=1.5 | 633 | 34.9% | 59.2% | 30.0% | 10.7% | 0.88R | -4.03 | 8.60R | 1.05R | 34.9% | 0.0% | 0.0% |
| RR=1.5/ATR=2.0 | 516 | 34.1% | 43.8% | 28.9% | 27.3% | 0.86R | 13.29 | 6.42R | 0.89R | 34.1% | 0.0% | 0.0% |
| RR=1.5/ATR=2.5 | 437 | 34.1% | 33.9% | 23.1% | 43.0% | 0.80R | -3.21 | 5.14R | 0.81R | 34.1% | 0.0% | 0.0% |
| RR=2.0/ATR=1.0 | 814 | 33.2% | 70.6% | 24.7% | 4.7% | 1.02R | 6.21 | 12.97R | 1.29R | 32.8% | 0.4% | 0.0% |
| RR=2.0/ATR=1.5 | 563 | 31.6% | 59.5% | 24.7% | 15.8% | 1.02R | 24.14 | 8.59R | 1.08R | 31.3% | 0.4% | 0.0% |
| RR=2.0/ATR=2.0 | 468 | 30.1% | 48.1% | 20.1% | 31.8% | 0.91R | -32.44 | 6.42R | 0.93R | 30.1% | 0.0% | 0.0% |
| RR=2.0/ATR=2.5 | 408 | 31.4% | 34.3% | 17.2% | 48.5% | 0.85R | -23.41 | 5.14R | 0.82R | 31.4% | 0.0% | 0.0% |
| RR=2.5/ATR=1.0 | 735 | 31.4% | 72.1% | 20.5% | 7.3% | 1.13R | -18.10 | 12.97R | 1.32R | 30.7% | 0.7% | 0.0% |
| RR=2.5/ATR=1.5 | 527 | 28.7% | 61.5% | 18.4% | 20.1% | 1.08R | -38.69 | 8.59R | 1.11R | 28.3% | 0.4% | 0.0% |
| RR=2.5/ATR=2.0 | 436 | 27.8% | 48.2% | 15.4% | 36.5% | 0.98R | -29.76 | 6.42R | 0.94R | 27.8% | 0.0% | 0.0% |
| RR=2.5/ATR=2.5 | 394 | 29.7% | 36.3% | 14.0% | 49.7% | 0.89R | -37.12 | 5.14R | 0.85R | 29.7% | 0.0% | 0.0% |

## MFE Distribution (V1 Baseline — 1.0ATR SL)

### RR 1.0x
| Range | % of Trades |
|---|---|
| 0.0_to_0.5rR | 49.8% |
| 0.5r_to_1rR | 13.6% |
| 1r_to_1.5rR | 22.2% |
| 1.5r_to_2rR | 7.5% |
| 2r_to_2.5rR | 3.2% |
| above_2.5rR | 3.7% |
### RR 1.5x
| Range | % of Trades |
|---|---|
| 0.0_to_0.5rR | 50.7% |
| 0.5r_to_1rR | 14.2% |
| 1r_to_1.5rR | 7.4% |
| 1.5r_to_2rR | 14.8% |
| 2r_to_2.5rR | 6.2% |
| above_2.5rR | 6.6% |
### RR 2.0x
| Range | % of Trades |
|---|---|
| 0.0_to_0.5rR | 50.7% |
| 0.5r_to_1rR | 13.0% |
| 1r_to_1.5rR | 6.9% |
| 1.5r_to_2rR | 5.5% |
| 2r_to_2.5rR | 12.1% |
| above_2.5rR | 11.8% |
### RR 2.5x
| Range | % of Trades |
|---|---|
| 0.0_to_0.5rR | 52.0% |
| 0.5r_to_1rR | 14.6% |
| 1r_to_1.5rR | 6.2% |
| 1.5r_to_2rR | 5.2% |
| 2r_to_2.5rR | 3.7% |
| above_2.5rR | 18.3% |

## MFE Distribution (V2 Candidate — 1.0ATR SL)

### RR 1.0x
| Range | % of Trades |
|---|---|
| 0.0_to_0.5rR | 47.8% |
| 0.5r_to_1rR | 12.9% |
| 1r_to_1.5rR | 21.9% |
| 1.5r_to_2rR | 8.0% |
| 2r_to_2.5rR | 3.9% |
| above_2.5rR | 5.5% |
### RR 1.5x
| Range | % of Trades |
|---|---|
| 0.0_to_0.5rR | 47.7% |
| 0.5r_to_1rR | 15.2% |
| 1r_to_1.5rR | 6.8% |
| 1.5r_to_2rR | 15.6% |
| 2r_to_2.5rR | 7.1% |
| above_2.5rR | 7.7% |
### RR 2.0x
| Range | % of Trades |
|---|---|
| 0.0_to_0.5rR | 49.4% |
| 0.5r_to_1rR | 14.1% |
| 1r_to_1.5rR | 6.6% |
| 1.5r_to_2rR | 4.7% |
| 2r_to_2.5rR | 12.7% |
| above_2.5rR | 12.5% |
### RR 2.5x
| Range | % of Trades |
|---|---|
| 0.0_to_0.5rR | 47.3% |
| 0.5r_to_1rR | 15.5% |
| 1r_to_1.5rR | 6.3% |
| 1.5r_to_2rR | 4.9% |
| 2r_to_2.5rR | 4.9% |
| above_2.5rR | 21.1% |

## Best Exit Configuration

- **V1 Best PnL**: `RR=2.0/ATR=2.0` (Net PnL 65.54)
- **V2 Best PnL**: `RR=1.0/ATR=1.0` (Net PnL 33.98)

## Trade Quality Analysis

### Key Metrics Explained
- **Avg MFE**: Average Maximum Favorable Excursion in R. If Avg MFE > RR, most trades went deep into profit before hitting SL.
- **Max MFE**: Best case R reached. If Max MFE >> RR, the strategy often had winners that didn't make it to TP.
- **Stopped Before 1R %**: % of trades that reversed and hit SL before making 1R. High % = SL too tight.
- **Reached 2R %**: % of trades that made it past 2R target. Low % = TP is too far.

## Recommended Exit Model Changes

Based on BACKTEST-007 data:
- **Recommended Next**: `PROJECT-STRATEGY-003` — Implement improved exit model (e.g. partial TP at 1.5R, ATR stop at 2.0x, break-even after 1R).

## Known Limitations
- Single 90-day period on one symbol.
- MFE/MAE computed on candle close, not intra-candle wick extreme.
- No slippage/fee impact modeled in MFE calculation.
- Fixed position sizing; real trading may vary.