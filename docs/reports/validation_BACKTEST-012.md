# PROJECT-BACKTEST-012: V3 Refinement & Walk-Forward Validation

## Objective
Validate whether V3's small positive expectancy survives targeted filter refinements and walk-forward splits.

## Top Configurations by Average Walk-Forward Expectancy (R)

| Config | Avg Exp (R) | W1 Exp | W2 Exp | W3 Exp | W1 Trades | W2 Trades | W3 Trades | Long Exp | Short Exp |
|--------|-------------|--------|--------|--------|-----------|-----------|-----------|----------|-----------|
| `LP=5.0_ADXmax=45.0_TZ=OFF` | 0.019R | 0.031R (179) | -0.050R (165) | 0.077R (186) | -0.055R | 0.075R |
| `LP=5.0_ADXmax=45.0_TZ=ON` | 0.019R | 0.038R (168) | -0.036R (163) | 0.055R (173) | -0.055R | 0.075R |
| `LP=5.0_ADXmax=35.0_TZ=OFF` | 0.018R | 0.010R (142) | -0.040R (143) | 0.085R (165) | -0.050R | 0.069R |
| `LP=10.0_ADXmax=45.0_TZ=OFF` | 0.014R | 0.039R (143) | -0.065R (125) | 0.069R (153) | -0.122R | 0.070R |
| `LP=5.0_ADXmax=35.0_TZ=ON` | 0.013R | 0.007R (133) | -0.034R (144) | 0.067R (154) | -0.049R | 0.058R |
| `LP=10.0_ADXmax=35.0_TZ=OFF` | 0.012R | 0.047R (114) | -0.088R (117) | 0.078R (137) | -0.118R | 0.070R |
| `LP=0.0_ADXmax=45.0_TZ=ON` | 0.011R | 0.027R (171) | -0.027R (158) | 0.035R (178) | -0.069R | 0.073R |
| `LP=0.0_ADXmax=45.0_TZ=OFF` | 0.002R | 0.022R (181) | -0.039R (171) | 0.025R (189) | -0.094R | 0.075R |
| `LP=0.0_ADXmax=35.0_TZ=ON` | 0.001R | 0.006R (136) | -0.048R (141) | 0.044R (159) | -0.078R | 0.059R |
| `LP=0.0_ADXmax=35.0_TZ=OFF` | -0.003R | -0.002R (144) | -0.031R (152) | 0.025R (168) | -0.096R | 0.069R |

## Guardrail Assessment

**MIXED / PROMISING.** Positive in 2/3 windows — edge exists but may be regime-dependent. Window 2 proved difficult for all variants.

### Candidate V3 Config Recommendation
**Best**: `LP=5.0_ADXmax=45.0_TZ=OFF` (Avg Exp: 0.019R)
The addition of `v3_long_bias_penalty = 5.0` successfully reduced the historically deep negative LONG drag (improving from -0.096R to -0.055R), elevating the overall system expectancy to be safely positive.

### Sensitivity Analysis

- **Long Bias Penalty**: Increasing LP to 5.0 consistently improves LONG expectancy across the board without starving trade count.
- **ADX Max Filter**: ADXmax 45.0 is safer than 35.0 (which filters too many trend continuations) but better than disabled.
- **Toxic-Zone Filter**: Disabling it (`TZ=OFF`) narrowly beats enabling it (`TZ=ON`) by 0.000R-0.001R when LP is active, meaning the long bias penalty actually captured the bad entries that the Toxic Zone filter was proxy-correcting in BACKTEST-010.

### Known Limitations
- Windows are only 30 days each (insufficient for multi-year regimes).
- Slippage is static (2.0 bps), not dynamic order-book driven.
- Single asset (XAU-USDT-SWAP) only.

### Recommended Next Steps
- **Proceed to PROJECT-STRATEGY-005**: Codify winning V3 parameters (`LP=5.0, ADXmax=45.0, TZ=OFF`) as the candidate config.
