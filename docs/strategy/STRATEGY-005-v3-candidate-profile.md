# PROJECT-STRATEGY-005: V3 Candidate Profile

## Status
**Research candidate. Not production-ready.**

This profile promotes the best V3 candidate from BACKTEST-012 as an explicit,
reproducible strategy configuration for further research. It does **not** change
V1 defaults, V2 defaults, or the generic V3 experimental profile.

## Candidate Label
`v3_candidate`

## Candidate Configuration

| Field | Value |
|---|---|
| `version` | `v3_candidate` |
| `v3_active` | `True` |
| `v3_long_bias_penalty` | `5.0` |
| `v3_max_adx` | `45.0` |
| `v3_reject_toxic_score` | `False` |
| `v3_min_adx` | `15.0` |
| `min_score` | `65.0` |
| `min_score_gap` | `15.0` |
| `improved_exit` | `True` |
| `ema_fast_period` / `ema_slow_period` | `50 / 200` |

## Rationale from BACKTEST-012

BACKTEST-012 tested walk-forward splits over the validated 90-day XAU-USDT-SWAP
15m dataset.

### Key findings
- Best candidate: `LP=5.0, ADXmax=45.0, TZ=OFF`
- Average walk-forward expectancy: `+0.019R`
- Result quality: **Mixed / Promising**
- Positive in **2 of 3** windows
- Window 2 remained negative for all tested variants

### Interpretation
- `v3_long_bias_penalty=5.0` reduced long-side drag materially, without killing trade count.
- `v3_max_adx=45.0` outperformed tighter ADX caps and helped avoid late-trend whipsaws.
- Toxic-zone rejection became redundant once the long-bias penalty was enabled, so the
  candidate disables it for simplicity and reproducibility.

## Important limitations
- This is **not** a production-ready profile.
- BACKTEST-012 showed a clear **W2 regime weakness**.
- Positive expectancy did **not** persist in all walk-forward windows.
- Single symbol, 90-day sample only.

## Reproducible instantiation

```python
from xauusdt.strategy.confluence import ConfluenceStrategy, make_v3_candidate_config

cfg = make_v3_candidate_config()
strategy = ConfluenceStrategy(cfg)
```

## What remains unchanged
- `ConfluenceConfig()` default V1 behavior remains unchanged
- V2 behavior remains unchanged
- `make_v3_config()` remains the generic experimental V3 profile

## Next step
**PROJECT-BACKTEST-013** — Market regime analysis for `v3_candidate`, focused on why W2 stayed negative across all tested V3 variants.
