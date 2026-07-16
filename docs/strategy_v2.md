# Confluence Strategy V2

**Status**: Active in codebase. Disabled in default config.

## Overview

ConfluenceStrategy v2 adds quality filters to reduce false breakouts and improve win rate,
based on evidence from BACKTEST-005 ablation analysis. The v2 filters don't change the scoring
mechanics; they act as a post-score "quality gate" to reject trades that meet the `min_score`
threshold but fail secondary trend-strength or momentum confirmation.

## Design Decisions (BACKTEST-005 Evidence)

| Finding | V2 Response |
|---|---|
| Baseline already produces 250 trades / 90 days | Do NOT lower `min_score`. Keep at 65.0. |
| `min_score=55` increases trades but degrades PnL | Quality over quantity. |
| `adx_min=25` yields best PnL (-197.78) | ADX filter is valuable. Added `adx_rising` as complementary filter. |
| Disabling `structure` drops trades to 44 | Structure is the core filter. Do not remove or weaken. |
| Disabling `swing` drops trades to 95 | Swing bonus is working correctly. Keep intact. |

## V2 Filters

### 1. `adx_rising`
Requires `current_adx > previous_adx`. This ensures the strategy only enters when trend strength is
actively building, not when ADX has plateaued or is declining (which indicates fading momentum
and a higher risk of false breakout).

- Config: `ConfluenceConfig(adx_rising=True)`
- Enabled for V2 config. Disabled for V1 (default).

### 2. `ema_slope_alignment`
Requires `current_ema_50 > previous_ema_50` for BUY entries, and `current_ema_50 < previous_ema_50`
for SELL entries. This confirms the fast EMA is moving in the direction of the proposed entry,
filtering out counter-slope entries that may be late in a trend rotation.

- Config: `ConfluenceConfig(ema_slope_alignment=True)`
- Enabled for V2 config. Disabled for V1 (default).

## Configuration Comparison

| Parameter | V1 (default) | V2 |
|---|---|---|
| `ema_fast_period` | 50 | 50 |
| `ema_slow_period` | 200 | 200 |
| `min_score` | 65.0 | 65.0 |
| `min_score_gap` | 15.0 | 15.0 |
| `adx_min` | 20.0 | **25.0** |
| `adx_rising` | `False` | **`True`** |
| `ema_slope_alignment` | `False` | **`True`** |
| `sl_atr_multiplier` | 1.5 | 1.5 |
| `risk_reward_ratio` | 2.0 | 2.0 |
| `version` | `"v1"` | `"v2"` |

## Versioning

The `ConfluenceConfig.version` attribute is set to `"v1"` or `"v2"` and included in all backtest
reports to clearly label which strategy version is being tested.

## Known Limitations

- V2 is evaluated only on 90-day XAU-USDT-SWAP 15m data (2026-04-16 to 2026-07-14).
- No walk-forward testing has been performed.
- `adx_rising` will reject trades on the exact candle where ADX peaks before a trend reversal,
  potentially missing the very best entry point in strong momentum bursts.
- `ema_slope_alignment` requires 2 consecutive EMA values to be computed; the filter is
  automatically skipped during the warmup phase where slope data is unavailable (resulting in a
  hard reject if `ema_slope_alignment=True`).
