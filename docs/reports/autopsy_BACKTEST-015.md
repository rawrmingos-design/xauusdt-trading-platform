# PROJECT-BACKTEST-015: W2 Entry-Context Autopsy

## Objective
Identify **which entry contexts** destroy `v3_candidate` expectancy in walk-forward **W2**,
by contrasting the same contexts in W1/W3. Diagnostics only — no re-tuning production defaults.

## Status
**Research diagnostics. Not production-ready. No candidate promotion in this task.**

- Config: `v3_candidate` + V2 quality flags (same baseline as BACKTEST-012/013/014)
- Candles: 8640 × 15m XAU-USDT-SWAP
- Metric: entry-grouped realized R (not dollar PnL sums)

## Window Performance (baseline)

| Window | N | Exp R | Win% | Long R | Short R | Long N | Short N |
| --- | --- | --- | --- | --- | --- | --- | --- |
| W1 | 140 | 0.045R | 42.1% | -0.029R | 0.112R | 67 | 73 |
| W2 | 139 | -0.070R | 43.2% | -0.105R | -0.030R | 74 | 65 |
| W3 | 142 | 0.128R | 50.0% | -0.051R | 0.297R | 69 | 73 |
| FULL | 421 | 0.035R | 45.1% | -0.063R | 0.132R | 210 | 211 |

## Cross-Window Context (key dimensions)

### side

| Context | W1 n/R | W2 n/R | W3 n/R | W2−ref |
| --- | --- | --- | --- | --- |
| SHORT | 73/0.112R | 65/-0.030R | 73/0.297R | -0.235R |
| LONG | 67/-0.029R | 74/-0.105R | 69/-0.051R | -0.065R |

### score_band

| Context | W1 n/R | W2 n/R | W3 n/R | W2−ref |
| --- | --- | --- | --- | --- |
| 80-84 **TOXIC-W2** | 3/0.501R | 6/-0.999R | 7/-0.190R | -1.155R |
| 65-69 | 3/1.026R | 3/0.306R | 4/0.239R | -0.326R |
| 75-79 | 18/-0.230R | 10/-0.416R | 16/-0.008R | -0.298R |
| 90+ | 28/0.129R | 19/0.004R | 26/0.155R | -0.139R |
| 70-74 | 76/0.008R | 89/-0.004R | 74/0.219R | -0.118R |
| 85-89 | 12/0.129R | 12/-0.018R | 15/-0.105R | -0.030R |

### ema_trend

| Context | W1 n/R | W2 n/R | W3 n/R | W2−ref |
| --- | --- | --- | --- | --- |
| UP | 43/-0.032R | 58/-0.178R | 49/-0.041R | -0.142R |
| DOWN | 97/0.079R | 81/0.007R | 93/0.217R | -0.141R |

### side_x_ema

| Context | W1 n/R | W2 n/R | W3 n/R | W2−ref |
| --- | --- | --- | --- | --- |
| SHORT|UP **TOXIC-W2** | 9/0.111R | 20/-0.092R | 10/0.170R | -0.232R |
| SHORT|DOWN | 64/0.112R | 45/-0.003R | 63/0.318R | -0.218R |
| LONG|UP | 34/-0.070R | 38/-0.224R | 39/-0.095R | -0.141R |
| LONG|DOWN | 33/0.014R | 36/0.020R | 30/0.006R | +0.010R |

### side_x_score

| Context | W1 n/R | W2 n/R | W3 n/R | W2−ref |
| --- | --- | --- | --- | --- |
| LONG|80-84 **TOXIC-W2** | 1/1.158R | 5/-1.009R | 5/-0.192R | -1.492R |
| SHORT|80-84 | 2/0.172R | 1/-0.950R | 2/-0.184R | -0.944R |
| SHORT|75-79 | 8/-0.303R | 4/-0.615R | 7/0.358R | -0.643R |
| SHORT|90+ | 15/0.294R | 8/0.016R | 13/0.430R | -0.346R |
| SHORT|65-69 | 3/1.026R | 3/0.306R | 4/0.239R | -0.326R |
| SHORT|70-74 | 38/-0.028R | 44/-0.006R | 37/0.445R | -0.214R |
| LONG|85-89 | 5/-0.455R | 7/-0.123R | 5/0.369R | -0.080R |
| LONG|75-79 | 10/-0.171R | 6/-0.284R | 9/-0.292R | -0.052R |
| LONG|70-74 | 38/0.044R | 45/-0.003R | 37/-0.006R | -0.022R |
| SHORT|85-89 | 7/0.546R | 5/0.130R | 10/-0.342R | +0.028R |
| LONG|90+ | 13/-0.060R | 11/-0.005R | 13/-0.120R | +0.085R |

### exit_reason

| Context | W1 n/R | W2 n/R | W3 n/R | W2−ref |
| --- | --- | --- | --- | --- |
| TP | 21/1.845R | 17/1.357R | 21/1.510R | -0.321R |
| SL | 83/-0.431R | 69/-0.486R | 83/-0.150R | -0.196R |
| SIGNAL | 36/0.092R | 53/0.014R | 38/-0.029R | -0.018R |

### conflict

| Context | W1 n/R | W2 n/R | W3 n/R | W2−ref |
| --- | --- | --- | --- | --- |
| ALIGNED **TOXIC-W2** | 140/0.045R | 139/-0.070R | 142/0.128R | -0.156R |

### adx_band

| Context | W1 n/R | W2 n/R | W3 n/R | W2−ref |
| --- | --- | --- | --- | --- |
| 20-24 **TOXIC-W2** | 34/0.198R | 44/-0.137R | 50/0.237R | -0.354R |
| 15-19 | 16/-0.158R | 20/-0.375R | 22/-0.133R | -0.230R |
| 40-44 | 11/0.047R | 13/0.028R | 4/0.409R | -0.200R |
| 30-39 **TOXIC-W2** | 48/-0.014R | 31/-0.065R | 38/0.084R | -0.100R |
| 25-29 | 31/0.070R | 31/0.175R | 28/0.158R | +0.060R |

## W2 Loss Mass Contribution

Sum of negative entry R in W2: **-57.50R**

### Loss share by side

| Key | Losers | Loss R sum | Share of W2 loss R |
| --- | --- | --- | --- |
| SHORT | 38 | -29.42R | 51.2% |
| LONG | 41 | -28.08R | 48.8% |

### Loss share by score_band

| Key | Losers | Loss R sum | Share of W2 loss R |
| --- | --- | --- | --- |
| 70-74 | 48 | -28.19R | 49.0% |
| 90+ | 10 | -8.64R | 15.0% |
| 75-79 | 7 | -7.54R | 13.1% |
| 85-89 | 7 | -6.09R | 10.6% |
| 80-84 | 6 | -6.00R | 10.4% |
| 65-69 | 1 | -1.04R | 1.8% |

### Loss share by ema_trend

| Key | Losers | Loss R sum | Share of W2 loss R |
| --- | --- | --- | --- |
| DOWN | 40 | -30.67R | 53.3% |
| UP | 39 | -26.82R | 46.7% |

### Loss share by side_x_ema

| Key | Losers | Loss R sum | Share of W2 loss R |
| --- | --- | --- | --- |
| SHORT|DOWN | 24 | -25.78R | 44.8% |
| LONG|UP | 25 | -23.19R | 40.3% |
| LONG|DOWN | 16 | -4.89R | 8.5% |
| SHORT|UP | 14 | -3.64R | 6.3% |

### Loss share by side_x_score

| Key | Losers | Loss R sum | Share of W2 loss R |
| --- | --- | --- | --- |
| SHORT|70-74 | 26 | -18.07R | 31.4% |
| LONG|70-74 | 22 | -10.12R | 17.6% |
| LONG|80-84 | 5 | -5.05R | 8.8% |
| LONG|90+ | 6 | -4.92R | 8.6% |
| LONG|85-89 | 4 | -4.17R | 7.2% |
| LONG|75-79 | 4 | -3.83R | 6.7% |
| SHORT|90+ | 4 | -3.73R | 6.5% |
| SHORT|75-79 | 3 | -3.71R | 6.5% |
| SHORT|85-89 | 3 | -1.92R | 3.3% |
| SHORT|65-69 | 1 | -1.04R | 1.8% |
| SHORT|80-84 | 1 | -0.95R | 1.7% |

### Loss share by exit_reason

| Key | Losers | Loss R sum | Share of W2 loss R |
| --- | --- | --- | --- |
| SL | 51 | -51.20R | 89.0% |
| SIGNAL | 28 | -6.30R | 11.0% |

## Counterfactual Filters (diagnostics only — not applied to production)

| Filter | W2 ΔR | W2 rem n/R | FULL ΔR | W1 ΔR | W3 ΔR | Rem W/L FULL |
| --- | --- | --- | --- | --- | --- | --- |
| toxic_score_75_84 | +0.073R | 16/-0.635R | +0.045R | +0.030R | +0.037R | 20/40 |
| ema_up | +0.078R | 58/-0.178R | +0.070R | +0.034R | +0.089R | 61/89 |
| long_only | +0.040R | 74/-0.105R | +0.097R | +0.067R | +0.169R | 89/121 |
| short_only | -0.035R | 65/-0.030R | -0.098R | -0.073R | -0.179R | 101/110 |
| long_and_ema_up | +0.058R | 38/-0.224R | +0.060R | +0.037R | +0.085R | 42/69 |
| short_and_ema_up | +0.004R | 20/-0.092R | +0.001R | -0.005R | -0.003R | 19/20 |
| toxic_score_or_ema_up | +0.123R | 63/-0.218R | +0.094R | +0.067R | +0.087R | 69/105 |
| conflict | +0.000R | 0/0.000R | +0.000R | +0.000R | +0.000R | 0/0 |
| signal_exit | -0.052R | 53/0.014R | +0.005R | -0.016R | +0.057R | 63/64 |

## Verdict

- Tags: `both_sides_negative_in_w2, toxic_mid_score_w2_specific, ema_up_toxic_in_w2, short_edge_regime_collapse`
- Best diagnostic counterfactual: **`toxic_score_or_ema_up`**
- Counterfactual class: **`actionable_context_filter_candidate`** (in-sample only)

### Key findings

1. **SHORT edge collapses in W2, not just LONG drag**
   - SHORT: W1 +0.112R → W2 **-0.030R** → W3 +0.297R (Δ W2−ref ≈ **-0.235R**)
   - LONG stays negative everywhere; W2 is worse but not the only problem

2. **Toxic mid-score is real and W2-amplified**
   - Score **80–84**: W2 **6 / -0.999R** (mostly LONG|80-84 = 5 / -1.009R)
   - Score **75–79**: W2 10 / -0.416R
   - Counterfactual drop score∈[75,84]: W2 **+0.073R**, FULL **+0.045R**, W1/W3 also up
   - Caveat: **small n** on 80–84 (6 entries) — high magnitude, low count

3. **EMA-UP is broadly toxic (not only W2)**
   - EMA-UP: W1 -0.032R, W2 **-0.178R**, W3 -0.041R
   - Drop EMA-UP: helps **all** windows (W2 +0.078R, FULL +0.070R)
   - Interaction: **LONG|UP** = 40% of W2 loss mass; **SHORT|UP** marked TOXIC-W2 vs positive W1/W3

4. **Volume of loss is still “normal” SHORT|DOWN + score 70–74**
   - SHORT|DOWN losers: **-25.8R (44.8%)** of W2 loss mass
   - Score 70–74 losers: **-28.2R (49%)**
   - So mid-score/EMA filters improve *expectancy* but do **not** erase the bulk SL mass

5. **Exit mix**
   - W2 has more SIGNAL exits (53 vs 36/38) and fewer clean TPs
   - **89% of W2 loss R** is full SL — entry quality / stop-out, not partial mismanagement

6. **Best combined diagnostic filter**
   | Filter | W2 ΔR | FULL ΔR | W1 ΔR | W3 ΔR | Notes |
   |---|---:|---:|---:|---:|---|
   | toxic_score_75_84 | +0.073 | +0.045 | +0.030 | +0.037 | cleanest, low removal |
   | ema_up | +0.078 | +0.070 | +0.034 | +0.089 | helps all; removes many |
   | toxic_score_or_ema_up | **+0.123** | **+0.094** | +0.067 | +0.087 | best lift; high removal |
   | long_only (drop all longs) | +0.040 | +0.097 | +0.067 | +0.169 | confirms long drag; nuclear |

### Interpretation
1. This task does **not** promote a new filter. It only ranks contexts.
2. Unlike BACKTEST-014’s rolling RANGE_CHOP gate, **score∈[75,84] and EMA-UP** are entry-local and lift W1/W2/W3 **in-sample**.
3. That is **necessary but not sufficient** for STRATEGY-007 — same 90-day path = optimistic.
4. Do **not** retune LP/ADX/regime thresholds on this sample as a “fix”.
5. Prefer **narrow** experimental filters first (`toxic_score_75_84` alone, then `block EMA-UP on LONG`, then OR-combo) over nuclear short-only.

### Recommended next step
1. **STRATEGY-007 (experimental only):** opt-in profile(s) on top of `v3_candidate`:
   - A: re-enable toxic score reject **[75, 84]** (candidate currently has it OFF)
   - B: block **LONG when `context_ema_trend=UP`** (or block all EMA-UP — harsher)
   - C: A∨B as research combo
2. **Hold-out / extended sample validation** before any promotion (new dates or longer history).
3. Keep ungated `v3_candidate` as baseline for side-by-side.

## Explicit non-claims
- Not production-ready
- No live trading / no production default change
- Counterfactuals are **what-if diagnostics**, not optimized strategy variants
- In-sample lift ≠ OOS edge

## Reproducibility
```bash
uv run python tools/run_backtest_015_w2_entry_autopsy.py
```

JSON: `docs/reports/autopsy_BACKTEST-015_20260726_073008.json`
