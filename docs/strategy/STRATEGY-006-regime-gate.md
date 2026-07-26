# PROJECT-STRATEGY-006: Regime Gate for V3 Candidate

## Status
**Research profile. Not production-ready.**

Adds an optional **RANGE_CHOP** entry gate on top of `v3_candidate`, based on
PROJECT-BACKTEST-013 evidence that W2 failure was a high-range / low-efficiency
chop regime — not a simple direction label problem.

## What changed
1. New pure module: `src/xauusdt/strategy/regime.py`
   - `MarketRegime`: `TREND` | `RANGE_CHOP` | `UNKNOWN`
   - `classify_regime(closes, highs, lows, config)` — no lookahead, no LLM
2. Opt-in config fields on `ConfluenceConfig` (all default **OFF**):
   - `v3_regime_gate`
   - `v3_block_range_chop`
   - lookback / efficiency / range thresholds
3. New factory: `make_v3_candidate_regime_gated_config()`
   - version label: `v3_candidate_regime_gated`
4. Ungated profiles unchanged:
   - V1 defaults
   - V2
   - `make_v3_config()` experimental
   - `make_v3_candidate_config()` (STRATEGY-005 baseline)

## Classifier definition
Over the last `lookback` closed bars (default 96 = 24h on 15m):

| Metric | Meaning |
|---|---|
| efficiency_ratio | `abs(net move) / sum(abs bar-to-bar moves)` |
| range_pct | `(high-low span) / start` |
| avg_candle_range_pct | mean(high-low) / start |

**RANGE_CHOP** when all hold:
- `efficiency_ratio < 0.30`
- `range_pct >= 0.035`
- `avg_candle_range_pct >= 0.0012`

Otherwise **TREND** (if enough history). Insufficient history → **UNKNOWN** (entries allowed).

## Gate behavior
When `v3_regime_gate=True` and `v3_block_range_chop=True`:
- New entries are blocked while label is `RANGE_CHOP`
- Rejection reason: `v3_regime_range_chop:eff=...,range=...,avg=...`
- Open positions are **not** force-closed by the gate (exit logic unchanged)

## Instantiation

```python
from xauusdt.strategy.confluence import (
    ConfluenceStrategy,
    make_v3_candidate_config,
    make_v3_candidate_regime_gated_config,
)

baseline = make_v3_candidate_config()          # gate OFF
gated = make_v3_candidate_regime_gated_config()  # gate ON
strategy = ConfluenceStrategy(gated)
```

## Evidence link
- BACKTEST-013: W2 both-sides negative; short edge collapse; widest/choppy path
- BACKTEST-014: side-by-side ungated vs gated walk-forward validation

## Explicit non-claims
- Not production default
- Not live-ready
- Thresholds are research defaults, not optimized
- Does not “solve” long drag permanently

## Next step
Run / review **PROJECT-BACKTEST-014** OOS walk-forward comparison before any further promotion.

### BACKTEST-014 result (recorded)
Default gate thresholds were **inconclusive / slightly harmful**:
- W2 Δ Exp R: **-0.012R**
- FULL Δ Exp R: **-0.014R**
- Chop bar share only ~2.7–8.6% → gate barely engaged
- **Do not promote** gated profile over ungated `v3_candidate`

Keep the module as experimental infrastructure for future regime designs (window-level labels, different features), not as a tuned production filter.
