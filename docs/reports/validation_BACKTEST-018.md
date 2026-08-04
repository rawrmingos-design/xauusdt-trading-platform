# PROJECT-BACKTEST-018: Extended Multi-Regime OOS (frozen 365d)

## Objective
Evaluate fixed `v3_candidate` vs `s7b` on **previously untouched** historical
periods from the frozen 365-day dataset. No re-fit, no new filters, no strategy changes.

## Status
**Research evaluation. Not production-ready.**

## Dataset
- Version: `ds_xau_15m_365d_v1`
- Fingerprint: `35008ecb67b095c0` (verified)
- Range: 2025-07-15 → 2026-07-14 | total **35040** candles
- Stored candles only; no OKX API calls

## Windows

| Window | Range | Role |
|---|---|---|
| **NEW_OOS_A** | 2025-07-15 → 2025-10-15 | **Primary untouched OOS** |
| **NEW_OOS_B** | 2025-10-16 → 2026-01-15 | **Primary untouched OOS** |
| PREVIOUS_OOS | 2026-01-16 → 2026-04-15 | Previously inspected (context only) |
| DISCOVERY | 2026-04-16 → 2026-07-15 | Contaminated discovery (context only) |

## Results (entry-grouped realized R)

| Segment | Candles | Baseline R (N, PF) | s7b R (N, Δ) | s7b removed |
|---|---:|---|---:|---:|
| NEW_OOS_A (primary untouched OOS) | 8832 | **-0.020R** (413, PF 0.959) | **-0.134R** (175, Δ -0.114) | 238 |
| NEW_OOS_B (primary untouched OOS) | 8736 | **0.021R** (429, PF 1.049) | **-0.058R** (215, Δ -0.079) | 214 |
| UNTOUCHED_AGG (search) | 17568 | **-0.001R** (856, PF 0.998) | **-0.095R** (396, Δ -0.094) | 460 |
| PREVIOUS_OOS (previously inspected validation context only) | 8544 | **0.140R** (395, PF 1.308) | **0.150R** (221, Δ +0.010) | 174 |
| DISCOVERY (contaminated discovery context only) | 8640 | **0.035R** (421, PF 1.083) | **0.084R** (312, Δ +0.049) | 109 |

## Long / Short

| Segment | Base Long | Base Short | s7b Long | s7b Short |
|---|---:|---:|---:|---:|
| NEW_OOS_A | 0.095R (413) | -0.247R | 0.294R | -0.202R |
| NEW_OOS_B | 0.060R (429) | -0.052R | -0.049R | -0.061R |
| UNTOUCHED_AGG | 0.074R (856) | -0.147R | 0.041R | -0.130R |

## Verdict: `freeze_strategy_shift_focus`

- **Untouched aggregate**: baseline -0.001R (N=856, PF=0.998) | s7b -0.095R (Δ -0.094, removed 460)
- NEW_OOS_A: baseline -0.020R, s7b -0.134R
- NEW_OOS_B: baseline 0.021R, s7b -0.058R

### Decision gate (user pre-registered)
| Condition | Verdict |
|---|---|
| V3 positive/stable, s7b not better | `v3_positive_stable_keep_primary` |
| s7b wins both untouched windows | `s7b_wins_consistently_promote_sibling` |
| Single window saves aggregate | `regime_dependent_continue_regime_diagnostics` |
| Both negative | `freeze_strategy_shift_focus` |
| Both positive & stable | paper/shadow harness while awaiting forward OOS |

## Explicit non-claims
- Not production-ready / not live
- Single symbol; untouched OOS is ~6 months of one market regime
- PREVIOUS_OOS and DISCOVERY are NOT evidence for promotion
- This run **consumes** the untouched historical OOS

## Reproducibility
```bash
uv run python tools/list_dataset_versions.py
uv run python tools/select_dataset.py --version ds_xau_15m_365d_v1
uv run python tools/run_backtest_018_extended_oos.py
```

JSON: `docs/reports/validation_BACKTEST-018_20260804_073954.json`
