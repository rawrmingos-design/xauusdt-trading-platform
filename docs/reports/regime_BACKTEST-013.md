# PROJECT-BACKTEST-013: Market Regime Analysis for V3 Candidate

## Objective

Explain why walk-forward Window 2 is negative for `v3_candidate` without re-optimizing parameters.

## Status

**Diagnostics only. Not production-ready. No parameter promotion.**

## Candidate Config (fixed)

| Field | Value |
|---|---|
| `version` | `v3_candidate` |
| `v3_long_bias_penalty` | `5.0` |
| `v3_max_adx` | `45.0` |
| `v3_reject_toxic_score` | `False` |
| `improved_exit` | `True` |
| `adx_rising` | `True` |
| `ema_slope_alignment` | `True` |
| `sl_atr_multiplier` | `1.5` |
| `risk_reward_ratio` | `2.5` |

## Window Market Regimes

| Window | Direction | Net Return | Range % | Avg Candle Range | Return Vol | Max DD % | Up Bars % |
|---|---|---:|---:|---:|---:|---:|---:|
| W1 | DOWN | -5.54% | 7.68% | 6.01 | 0.001151 | 7.24% | 48.5% |
| W2 | DOWN | -5.50% | 12.42% | 6.76 | 0.001311 | 12.07% | 48.3% |
| W3 | DOWN | -5.56% | 10.10% | 6.43 | 0.001394 | 9.51% | 48.3% |

## Window Strategy Performance (`v3_candidate`)

| Window | Entries | Exp R | Long R | Short R | WR | Avg ADX | Conflict % | Net PnL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| W1 | 140 | 0.040R | -0.029R | 0.102R | 41.4% | 28.6 | 0.0% | -6.55 |
| W2 | 133 | -0.063R | -0.079R | -0.043R | 44.4% | 27.3 | 0.0% | -4.23 |
| W3 | 142 | 0.101R | -0.086R | 0.259R | 48.6% | 26.0 | 0.0% | 34.45 |
| FULL | 421 | 0.035R | -0.063R | 0.132R | 45.1% | 27.3 | 0.0% | 25.25 |

## Why W2 Failed

**Primary failure mode:** `both_sides_negative`

**Robustness label:** `mixed_regime_dependent` (2/3 windows positive)

### Evidence findings

- W2 has the highest average candle range (volatility expansion)
- W2 has the smallest net directional move (more range-bound)
- W2 SHORT expectancy is worst (-0.043R) vs W1/W3

### W2 deltas vs W1 / W3

| Metric | W2-W1 | W2-W3 |
|---|---:|---:|
| Expectancy R | -0.1022 | -0.1640 |
| Long R | -0.0499 | 0.0070 |
| Short R | -0.1454 | -0.3024 |
| Net return % | 0.0343 | 0.0620 |
| Return vol | 0.0002 | -0.0001 |
| Avg entry ADX | -1.3175 | 1.3247 |

## Entry Quality Breakdown by Window

### W1

- Exit reasons: `{'SL': 82, 'PARTIAL_TP': 39, 'SIGNAL': 36, 'TP': 21, 'EOL': 1}`
- Score buckets: `{'65-69': {'entries': 3, 'expectancy_r': 1.0261946755226399, 'win_rate': 0.3333333333333333}, '70-74': {'entries': 76, 'expectancy_r': -0.0008178752387028204, 'win_rate': 0.4473684210526316}, '75-79': {'entries': 18, 'expectancy_r': -0.22966349750375067, 'win_rate': 0.2222222222222222}, '80-84': {'entries': 3, 'expectancy_r': 0.5008982699489828, 'win_rate': 0.6666666666666666}, '85-89': {'entries': 12, 'expectancy_r': 0.12886196254811916, 'win_rate': 0.3333333333333333}, '90+': {'entries': 28, 'expectancy_r': 0.12926331025997878, 'win_rate': 0.4642857142857143}}`
- ADX buckets: `{'15-20': {'entries': 16, 'expectancy_r': -0.15829820632669764, 'win_rate': 0.3125}, '20-25': {'entries': 34, 'expectancy_r': 0.17787777029152968, 'win_rate': 0.38235294117647056}, '25-30': {'entries': 31, 'expectancy_r': 0.07047235686089529, 'win_rate': 0.4838709677419355}, '30-35': {'entries': 27, 'expectancy_r': 0.015075823951544935, 'win_rate': 0.4444444444444444}, '35-40': {'entries': 21, 'expectancy_r': -0.05085566384434378, 'win_rate': 0.3333333333333333}, '40-45': {'entries': 11, 'expectancy_r': 0.04655448579360748, 'win_rate': 0.5454545454545454}}`
- Structure buckets: `{'bearish': {'entries': 44, 'expectancy_r': 0.09804029282747856, 'win_rate': 0.4090909090909091}, 'bullish': {'entries': 96, 'expectancy_r': 0.012886674131809588, 'win_rate': 0.4166666666666667}}`
- EMA trend buckets: `{'DOWN': {'entries': 97, 'expectancy_r': 0.07156835906649521, 'win_rate': 0.422680412371134}, 'UP': {'entries': 43, 'expectancy_r': -0.032354354148540876, 'win_rate': 0.3953488372093023}}`

### W2

- Exit reasons: `{'PARTIAL_TP': 32, 'SL': 65, 'SIGNAL': 51, 'TP': 16, 'EOL': 1}`
- Score buckets: `{'65-69': {'entries': 2, 'expectancy_r': -0.20471234368027924, 'win_rate': 0.5}, '70-74': {'entries': 87, 'expectancy_r': 0.024666706091052436, 'win_rate': 0.4827586206896552}, '75-79': {'entries': 8, 'expectancy_r': -0.5415131308763225, 'win_rate': 0.25}, '80-84': {'entries': 5, 'expectancy_r': -1.0176850760270604, 'win_rate': 0.0}, '85-89': {'entries': 12, 'expectancy_r': -0.017616988502857006, 'win_rate': 0.4166666666666667}, '90+': {'entries': 19, 'expectancy_r': -0.022380547994174084, 'win_rate': 0.47368421052631576}}`
- ADX buckets: `{'15-20': {'entries': 19, 'expectancy_r': -0.13523635362507255, 'win_rate': 0.42105263157894735}, '20-25': {'entries': 41, 'expectancy_r': -0.20958928894040735, 'win_rate': 0.34146341463414637}, '25-30': {'entries': 29, 'expectancy_r': 0.1716968342251383, 'win_rate': 0.5862068965517241}, '30-35': {'entries': 17, 'expectancy_r': -0.05694077751732751, 'win_rate': 0.47058823529411764}, '35-40': {'entries': 14, 'expectancy_r': -0.1095305088691544, 'win_rate': 0.42857142857142855}, '40-45': {'entries': 13, 'expectancy_r': 0.0280213658737176, 'win_rate': 0.46153846153846156}}`
- Structure buckets: `{'bearish': {'entries': 44, 'expectancy_r': -0.05616812707683495, 'win_rate': 0.36363636363636365}, 'bullish': {'entries': 89, 'expectancy_r': -0.06572121760817676, 'win_rate': 0.48314606741573035}}`
- EMA trend buckets: `{'DOWN': {'entries': 76, 'expectancy_r': 0.0005470872237282677, 'win_rate': 0.5131578947368421}, 'UP': {'entries': 57, 'expectancy_r': -0.14670464188617224, 'win_rate': 0.3508771929824561}}`

### W3

- Exit reasons: `{'SIGNAL': 41, 'PARTIAL_TP': 44, 'TP': 21, 'SL': 80}`
- Score buckets: `{'65-69': {'entries': 5, 'expectancy_r': 0.02624184177653156, 'win_rate': 0.8}, '70-74': {'entries': 77, 'expectancy_r': 0.19889151283673798, 'win_rate': 0.4675324675324675}, '75-79': {'entries': 14, 'expectancy_r': -0.011024436014618713, 'win_rate': 0.5}, '80-84': {'entries': 7, 'expectancy_r': -0.19010233380531352, 'win_rate': 0.42857142857142855}, '85-89': {'entries': 15, 'expectancy_r': -0.10498905798314288, 'win_rate': 0.4666666666666667}, '90+': {'entries': 24, 'expectancy_r': 0.08429323685179242, 'win_rate': 0.5}}`
- ADX buckets: `{'15-20': {'entries': 21, 'expectancy_r': -0.08614109816845827, 'win_rate': 0.38095238095238093}, '20-25': {'entries': 53, 'expectancy_r': 0.20625826051503265, 'win_rate': 0.5283018867924528}, '25-30': {'entries': 27, 'expectancy_r': 0.12116605065269274, 'win_rate': 0.5185185185185185}, '30-35': {'entries': 22, 'expectancy_r': -0.07378405621672958, 'win_rate': 0.45454545454545453}, '35-40': {'entries': 16, 'expectancy_r': 0.1761781545485746, 'win_rate': 0.4375}, '40-45': {'entries': 3, 'expectancy_r': 0.2730632405915156, 'win_rate': 0.6666666666666666}}`
- Structure buckets: `{'bearish': {'entries': 46, 'expectancy_r': 0.08863370987458412, 'win_rate': 0.5652173913043478}, 'bullish': {'entries': 96, 'expectancy_r': 0.10762342100973574, 'win_rate': 0.4479166666666667}}`
- EMA trend buckets: `{'DOWN': {'entries': 94, 'expectancy_r': 0.20616389817267722, 'win_rate': 0.48936170212765956}, 'UP': {'entries': 48, 'expectancy_r': -0.10355015327221163, 'win_rate': 0.4791666666666667}}`

## Interpretation

This task does **not** re-tune LP / ADX / toxic-zone. It only diagnoses whether W2 negativity is a market-regime problem rather than a broken candidate config.

### What W2 is (and is not)
- All three windows are **DOWN** overall (~-5.5% net return). Direction label alone does **not** explain W2.
- W2 is the **widest, choppiest** window: highest price range (12.4% vs 7.7%/10.1%) and highest average candle range.
- W2 max drawdown of the price path is also worst (12.1% vs 7.2%/9.5%).

### What broke for the strategy
1. **Both sides negative in W2** (`LONG -0.079R`, `SHORT -0.043R`). This is not the usual long-only drag.
2. **SHORT edge collapses.** SHORT is the candidate's main edge in W1 (+0.102R) and W3 (+0.259R), but dies in W2 (-0.043R). Delta W2-W3 SHORT = **-0.30R**.
3. **Mid-score toxic zone reappears hard in W2** even with toxic-zone filter off:
   - score 75–79: **-0.54R**
   - score 80–84: **-1.02R**
   Those buckets are far worse in W2 than in W1/W3.
4. **EMA-UP entries fail in W2** (`-0.147R`) while EMA-DOWN is flat (`+0.001R`). Against a wide down-chop path, long-biased / counter-trend setups are punished.
5. **More noise exits in W2**: SIGNAL exits rise (51 vs 36 in W1), full TP falls (16 vs 21). Trend-following hold quality degrades.

### Conclusion
`v3_candidate` is **mixed / regime-dependent**, not broken globally.
W2 is a **high-range, high-chop down path** where the candidate's short-trend edge fails and mid-score entries become toxic again.
Parameter re-tuning is the wrong next step. Regime detection / entry gating is the right next step.

## Known Limitations

- 90 days only; three 30-day windows are coarse regime labels
- No external macro labels (FOMC, DXY shock, etc.)
- ATR/ADX are strategy-internal; market regime here uses price path proxies
- Single symbol XAU-USDT-SWAP

## Recommended Next Steps

1. Build explicit regime classifier (trend vs range vs high-vol chop) before any new filter tuning.
2. Gate `v3_candidate` entries by regime, or reduce size/disable in range-chop windows.
3. Do **not** promote production defaults until regime filter is validated out-of-sample.

## Reproducibility

```bash
uv run python tools/run_backtest_013_regime_analysis.py
```
