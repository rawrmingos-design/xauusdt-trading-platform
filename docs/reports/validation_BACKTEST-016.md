# PROJECT-BACKTEST-016: STRATEGY-007 A/B/C Validation

## Objective
Compare fixed experimental profiles **s7a / s7b / s7c** against baseline `v3_candidate` without re-fitting thresholds.

## Status
**Research validation only. Not production-ready.**

## Hold-out honesty
> W3 (last 1/3) is a **chronological pseudo hold-out**. Discovery in BACKTEST-015 used the **full** 90-day path, so W3 is **contaminated**. This is **not** true external OOS. Extended history required for real OOS.

- Threshold re-fit: **No** (fixed [75,84] and LONG|EMA-UP from BT-015)
- Candles: 8640 × 15m

## Profiles
| Variant | Factory | Filters |
|---|---|---|
| baseline | `make_v3_candidate_config()` | — |
| s7a | `make_v3_candidate_s7a_config()` | toxic [75,84] |
| s7b | `make_v3_candidate_s7b_config()` | block LONG when EMA-UP |
| s7c | `make_v3_candidate_s7c_config()` | A + B |

## Walk-Forward Expectancy

| Window | Baseline R (N) | s7a R (Δ, N) | s7b R (Δ, N) | s7c R (Δ, N) |
|---|---:|---:|---:|---:|
| W1 | 0.040R (140) | 0.048R (+0.008, 132) | 0.059R (+0.019, 107) | 0.031R (-0.009, 101) |
| W2 | -0.063R (133) | -0.044R (+0.019, 132) | -0.014R (+0.048, 95) | 0.008R (+0.070, 95) |
| W3 | 0.101R (142) | 0.071R (-0.030, 133) | 0.183R (+0.081, 107) | 0.154R (+0.052, 104) |
| FULL | 0.035R (421) | 0.036R (+0.001, 401) | 0.084R (+0.049, 312) | 0.074R (+0.039, 302) |

## Long / Short (expectancy R)

| Window | Baseline L/S | s7a L/S | s7b L/S | s7c L/S |
|---|---|---|---|---|
| W1 | -0.029/0.102 | -0.007/0.100 | 0.014/0.079 | -0.065/0.075 |
| W2 | -0.079/-0.043 | -0.075/-0.007 | 0.062/-0.060 | 0.062/-0.025 |
| W3 | -0.086/0.259 | -0.108/0.222 | 0.023/0.242 | 0.023/0.204 |
| FULL | -0.063/0.132 | -0.059/0.131 | 0.030/0.109 | 0.001/0.109 |

## Verdict: `promote_research_s7b_same_sample_only`

- Rank by W3 then FULL: **s7b > s7c > s7a**
- W2 deltas: s7a +0.019R, s7b +0.048R, s7c +0.070R
- W3 (pseudo-holdout) deltas: s7a -0.030R, s7b +0.081R, s7c +0.052R
- FULL deltas: s7a +0.001R, s7b +0.049R, s7c +0.039R

### Profile read-out

| Profile | Takeaway |
|---|---|
| **s7b** (block LONG\|EMA-UP) | Best balanced lift: W2/W3/FULL all up. FULL LONG flips **-0.063R → +0.030R**. Removes ~109/421 entries (~26%). **Preferred research profile.** |
| **s7c** (A+B) | Best W2 (+0.070, flips slightly positive) but worse than s7b on W3/FULL; more removal (~28%). No free lunch vs B alone. |
| **s7a** (toxic [75,84]) | Mild W2 help; **hurts W3 (-0.030R)**; FULL ≈ flat. Engine path-dependency weaker than BT-015 post-hoc trade drop. **Do not promote over baseline.** |

### Why engine ≠ BT-015 counterfactual
BACKTEST-015 dropped trades *after* the fact. STRATEGY-007 filters *at entry*, which changes position occupancy and subsequent signals. Expect smaller / reordered effects — especially for toxic-score alone (s7a removed only 20 FULL entries vs broader post-hoc sets).

### Decision guide
| Outcome | Action |
|---|---|
| Helps W2 + FULL + W3 | Keep as research profile; still need true OOS |
| Helps W2 only | Reject as global default |
| High removal, similar R | Prefer narrower profile (B over C) |
| True OOS unavailable | **Do not promote to production** |

### Recommended research freeze
1. Keep **`v3_candidate`** as baseline.
2. Track **`v3_candidate_s7b`** as best experimental sibling (same-sample only).
3. Do **not** change production defaults.
4. Next evidence step: **true external OOS** (more history / later dates), not more filters on this 90d.

## Explicit non-claims
- Not production-ready
- Same-sample re-measurement is optimistic
- W3 is contaminated pseudo-holdout, not true OOS
- No live trading / no production default change

## Reproducibility
```bash
uv run python tools/run_backtest_016_s7_validation.py
```

JSON: `docs/reports/validation_BACKTEST-016_20260726_075855.json`
