# PROJECT-STRATEGY-007: V3 Candidate Context Filters (A/B/C)

## Status
**Research experimental profiles. Not production-ready.**

Adds three opt-in profiles on top of `v3_candidate` from BACKTEST-015
entry-context autopsy. **No production default changes.** Baseline
`v3_candidate` remains the research baseline.

## Evidence
BACKTEST-015 (same 90-day sample — optimistic):

| Filter idea | W2 ΔR | FULL ΔR |
|---|---:|---:|
| A toxic [75,84] | +0.073 | +0.045 |
| B drop EMA-UP (all) / LONG\|UP mass | +0.078 / material | +0.070 |
| C A∨B (toxic or EMA-UP) | +0.123 | +0.094 |

This task **implements** those ideas as explicit factories. It does **not**
re-fit thresholds.

## Profiles

| Profile | Factory | version | Changes vs `v3_candidate` |
|---|---|---|---|
| Baseline | `make_v3_candidate_config()` | `v3_candidate` | — |
| **A** | `make_v3_candidate_s7a_config()` | `v3_candidate_s7a` | `v3_reject_toxic_score=True` [75,84] |
| **B** | `make_v3_candidate_s7b_config()` | `v3_candidate_s7b` | `v3_block_long_ema_up=True` |
| **C** | `make_v3_candidate_s7c_config()` | `v3_candidate_s7c` | A + B |

Shared (unchanged from candidate):
- `v3_long_bias_penalty=5.0`
- `v3_max_adx=45.0`
- toxic zone OFF on baseline; ON on A/C
- `improved_exit=True`
- regime gate OFF

## New config field
- `v3_block_long_ema_up: bool = False` (default OFF for V1/V2/V3/candidate)

When True and side is buy: reject if `ema_9 > ema_21` (same definition as
backtest `context_ema_trend == "UP"`).

Rejection reason: `v3_block_long_ema_up:ema9=...>ema21=...`

## Instantiation

```python
from xauusdt.strategy.confluence import (
    ConfluenceStrategy,
    make_v3_candidate_config,
    make_v3_candidate_s7a_config,
    make_v3_candidate_s7b_config,
    make_v3_candidate_s7c_config,
)

baseline = make_v3_candidate_config()
s7a = make_v3_candidate_s7a_config()
s7b = make_v3_candidate_s7b_config()
s7c = make_v3_candidate_s7c_config()
```

## Validation protocol (BACKTEST-016)
1. **No threshold search** — fixed filters only.
2. Side-by-side walk-forward W1/W2/W3/FULL vs baseline.
3. Chronological **pseudo hold-out**: last 1/3 (W3) treated as evaluation slice
   with explicit contamination warning (discovery used full 90d in BT-015).
4. True external OOS needs longer history — out of scope if unavailable.

## Explicit non-claims
- Not production default
- Not live-ready
- In-sample / same-sample re-measurement ≠ true OOS
- Profile C high removal; may look better only by cutting trades

## Unchanged
- V1 defaults, V2, `make_v3_config()`, `make_v3_candidate_config()`, regime-gated profile

## BACKTEST-016 result (recorded)

Verdict: **`promote_research_s7b_same_sample_only`**

| Window | Baseline | s7a Δ | s7b Δ | s7c Δ |
|---|---:|---:|---:|---:|
| W1 | +0.040R | +0.008 | **+0.019** | -0.009 |
| W2 | -0.063R | +0.019 | **+0.048** | +0.070 |
| W3* | +0.101R | -0.030 | **+0.081** | +0.052 |
| FULL | +0.035R | +0.001 | **+0.049** | +0.039 |

\*W3 = contaminated pseudo-holdout (BT-015 used full sample).

- **Preferred research sibling:** `v3_candidate_s7b` (block LONG when EMA-UP)
- **s7a:** do not promote (hurts W3)
- **s7c:** helps W2 most but inferior to s7b on W3/FULL after removal cost
- **Still not production** — need true external OOS