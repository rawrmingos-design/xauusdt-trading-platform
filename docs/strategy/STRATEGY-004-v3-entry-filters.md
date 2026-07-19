# PROJECT-STRATEGY-004: Confluence Strategy v3 (Experimental Entry Filters)

## Status
**Experimental.** Do not promote v3 to production defaults until BACKTEST-011 validates it.

## Motivation (from BACKTEST-010)
BACKTEST-010 entry diagnostics showed that exit quality is no longer the primary failure mode.
Entry quality is. Key evidence:

| Finding | Evidence | v3 Filter |
|---------|----------|-----------|
| Mid-score "toxic zone" | Score 75–84 consistently negative R on V1/V2 | `v3_reject_toxic_score` (default zone 75–84) |
| Dead markets | ADX < 15 negative expectancy | `v3_min_adx=15` |
| Late-trend whipsaws | ADX > 40 weak/negative | `v3_max_adx=40` |
| LONG worse than SHORT (V1) | LONG −0.07R vs SHORT +0.06R | `v3_long_bias_penalty` (optional; default 0) |
| EMA/structure conflict | Already 0% (engine filters natively) | no new filter needed |

### Important caveat on toxic-zone rejection
Score 75–84 being toxic is **counter-intuitive**. A plausible explanation is late-entry:
high confluence appears after the move is already underway. Treat this filter as experimental
and re-validate on BACKTEST-011 (trade-off: losers removed vs winners removed).

## Design principles
1. **v1/v2 defaults unchanged.** `ConfluenceConfig()` still has `v3_active=False`.
2. **v3 is opt-in** via `make_v3_config()` or explicit `ConfluenceConfig(v3_active=True, ...)`.
3. **Configurable, not hard-coded magic.** Toxic zone bounds, ADX band, long penalty are fields.
4. **Explainable rejections.** `strategy.get_last_rejection_reasons()` returns structured strings.
5. **Compatible with improved exit** (`improved_exit=True` in default v3 profile).

## Instantiation

```python
from xauusdt.strategy.confluence import ConfluenceStrategy, make_v3_config

# Default experimental v3 profile
cfg = make_v3_config()
strategy = ConfluenceStrategy(cfg)

# Customize filters
cfg = make_v3_config(
    v3_reject_toxic_score=True,
    v3_toxic_score_min=75.0,
    v3_toxic_score_max=84.0,
    v3_min_adx=15.0,
    v3_max_adx=40.0,
    v3_long_bias_penalty=0.0,  # set 100.0 to hard-disable LONGs
)
```

## Filter summary

| Flag / field | Default (v3 factory) | Effect |
|--------------|----------------------|--------|
| `v3_active` | `True` | Master switch for all v3 filters |
| `v3_reject_toxic_score` | `True` | Reject scores in `[v3_toxic_score_min, v3_toxic_score_max]` |
| `v3_toxic_score_min` / `_max` | 75 / 84 | Bounds of experimental toxic zone |
| `v3_min_adx` | 15 | Reject ADX below this |
| `v3_max_adx` | 40 | Reject ADX above this |
| `v3_long_bias_penalty` | 0 | Subtract from buy_score; ≥100 hard-disables LONG |

## Rejection reason examples
- `v3_toxic_score_zone:80.0_in_[75,84]`
- `v3_adx_below_min:12.0<15`
- `v3_adx_above_max:45.0>40`
- `v3_long_entries_disabled`
- `v2_quality_failed:buy` (when v2 filters also active)

## What this PR does **not** do
- Does not change v1/v2 production defaults
- Does not run full 90-day comparison (that is BACKTEST-011)
- Does not claim v3 is profitable
- Does not change exit model logic

## Next step
**PROJECT-BACKTEST-011** — side-by-side V1 / V2 / V3 on the 90-day OKX dataset.
Required metrics: trade count, WR, PF, net PnL, max DD, expectancy, long/short split,
toxic-zone trades discarded, winners discarded, ADX band impact.
