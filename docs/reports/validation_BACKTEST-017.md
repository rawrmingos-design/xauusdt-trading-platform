# PROJECT-BACKTEST-017: True External OOS (v3_candidate vs s7a/b/c)

## Objective
Test whether STRATEGY-007 profiles (especially **s7b**) survive on data
**never used** in BT-011..016 filter design.

## Status
**Research validation. Not production-ready.**

## Data split

| Segment | Range | Role |
|---|---|---|
| **OOS_FULL** | 2026-01-16 → 2026-04-16 | **True pre-discovery OOS** |
| DISCOVERY_FULL | 2026-04-16 → 2026-07-15 | Contaminated (design sample) |

- Backfill: 180d 15m, gaps=0, total candles loaded: **17280**
- OOS candles: **8640** | Discovery candles: **8640**
- Threshold re-fit: **No**
- Matrix: 4 variants × (OOS + discovery) + baseline/s7b on OOS thirds

## Primary comparison

| Segment | Baseline R (N) | s7a R (Δ, N) | s7b R (Δ, N) | s7c R (Δ, N) |
|---|---:|---:|---:|---:|
| OOS_FULL | 0.138R (399) | 0.123R (-0.014, 380) | 0.148R (+0.010, 222) | 0.110R (-0.028, 211) |
| DISCOVERY_FULL | 0.035R (421) | 0.036R (+0.001, 401) | 0.084R (+0.049, 312) | 0.074R (+0.039, 302) |

## OOS thirds (stability — baseline vs s7b only)

| Segment | Baseline R (N) | s7b R (Δ, N) |
|---|---:|---:|
| OOS_T1 | 0.061R (117) | -0.054R (-0.115, 42) |
| OOS_T2 | 0.184R (125) | 0.187R (+0.003, 70) |
| OOS_T3 | 0.120R (135) | 0.123R (+0.004, 90) |

## Long / Short on OOS_FULL

| Variant | Long R | Short R |
|---|---:|---:|
| baseline | 0.090R | 0.199R |
| s7a | 0.124R | 0.122R |
| s7b | -0.004R | 0.182R |
| s7c | -0.004R | 0.137R |

## Verdict: `s7b_non_negative_oos_keep_research`

- Discovery s7b Δ: **+0.049R** (contaminated, expect positive)
- **True OOS s7b Δ: +0.010R** (base 0.138R → s7b 0.148R)
- OOS thirds s7b > 0: **2/3**
- OOS thirds s7b beats baseline: **2/3**
- Entries removed on OOS (s7b): **177** (399 → 222)

### Profile read-out (true OOS gate)

| Profile | OOS Δ | OOS abs | Decision |
|---|---:|---:|---|
| **baseline** `v3_candidate` | — | **+0.138R** | Strong on pre-discovery; remains research baseline |
| **s7b** | **+0.010R** | +0.148R | Non-negative OOS; modest lift; **keep research sibling only** |
| s7a | **-0.014R** | +0.123R | Fails true OOS vs baseline |
| s7c | **-0.028R** | +0.110R | Fails true OOS; combo overfits discovery |

### Stability caveat (critical)
- **OOS_T1**: s7b **−0.054R** vs baseline **+0.061R** (Δ **−0.115R**) — early OOS third destroyed
- OOS_T2/T3: s7b ≈ baseline (flat lift)
- s7b removes **~44%** OOS entries (399→222). Lift is mostly trade-thinning, not robust edge expansion.
- On OOS, s7b **LONG expectancy ≈ 0** (−0.004R) while baseline LONG still **+0.090R** — filter trades away good longs outside discovery regime.

### Research freeze after BT-017
1. Keep **`v3_candidate`** as primary research baseline (best absolute OOS R among clean profiles).
2. Keep **`v3_candidate_s7b`** experimental only — discovery lift did **not** fully transfer; OOS Δ tiny + T1 failure.
3. Drop promotion path for **s7a / s7c** (negative true OOS Δ).
4. **Do not** change production defaults. **Do not** design new filters from this OOS (no peeking).
5. Next evidence needs longer multi-regime history or post-discovery forward window — not more filters on 90d discovery.

### Decision guide
| Verdict | Action |
|---|---|
| `s7b_holds_on_true_oos` | Keep s7b as preferred research sibling; still not prod |
| `s7b_non_negative_oos_keep_research` | Keep experimental; need more history |
| `s7b_fails_true_oos_revert_baseline` | Drop s7b promotion path; freeze on v3_candidate |
| `s7b_turns_oos_negative` | Do not use s7b; baseline safer on OOS |
| `oos_inconclusive` | Need longer OOS / more symbols |

### Interpretation rules
1. Discovery lift alone is **not** evidence (contaminated).
2. True OOS is the primary gate for s7b.
3. Large trade removal with flat/negative OOS Δ = not useful.
4. No new filters designed from OOS in this task (no peeking loop).

## Explicit non-claims
- Not production-ready / not live
- Single symbol, ~90d OOS only
- No production default change

## Reproducibility
```bash
# Ensure 180d candles exist:
uv run python tools/run_backfill.py --days 180 --granularity 15m
uv run python tools/run_backtest_017_external_oos.py
```

JSON: `docs/reports/validation_BACKTEST-017_20260726_093610.json`
