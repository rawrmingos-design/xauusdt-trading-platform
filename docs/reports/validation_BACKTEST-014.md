# PROJECT-BACKTEST-014: Regime-Gated V3 Candidate Walk-Forward Validation

## Objective
Validate whether blocking entries in RANGE_CHOP improves W2 without destroying W1/W3 edge.

## Status
**Research validation only. Not production-ready.**

## Variants
| Variant | Factory | Gate |
|---|---|---|
| ungated | `make_v3_candidate_config()` | OFF |
| regime_gated | `make_v3_candidate_regime_gated_config()` | block RANGE_CHOP |

## Walk-Forward Comparison

| Window | Ungated Exp R | Gated Exp R | Δ Exp R | Ungated N | Gated N | Removed | Chop % |
|---|---:|---:|---:|---:|---:|---:|---:|
| W1 | 0.040R | 0.027R | -0.013R | 140 | 138 | 2 | 2.7% |
| W2 | -0.063R | -0.074R | -0.012R | 133 | 121 | 12 | 8.5% |
| W3 | 0.101R | 0.079R | -0.022R | 142 | 139 | 3 | 8.6% |
| FULL | 0.035R | 0.021R | -0.014R | 421 | 404 | 17 | 6.4% |

## Long / Short Breakdown

| Window | Ungated L/S | Gated L/S |
|---|---|---|
| W1 | -0.029R / 0.102R | -0.072R / 0.118R |
| W2 | -0.079R / -0.043R | -0.074R / -0.075R |
| W3 | -0.086R / 0.259R | -0.122R / 0.251R |
| FULL | -0.063R / 0.132R | -0.085R / 0.120R |

## Verdict: `inconclusive` (gate does **not** help under default thresholds)

- Positive windows ungated: **2/3**
- Positive windows gated: **2/3**
- W2 delta: **-0.012R** (worse, not better)
- FULL delta: **-0.014R**
- Entries removed FULL: **17 / 421** (~4%)

### What this means
1. The **infrastructure is correct** (classifier + opt-in gate + factories + tests).
2. Default research thresholds fire **rarely** on this 90-day path (chop share only 2.7–8.6% of bars).
3. The few blocked entries were **not** the toxic W2 mass — W2 stayed negative and slightly worsened.
4. Therefore: **do not promote** `v3_candidate_regime_gated` as an improved candidate yet.
5. Keep the gate as an **experimental tool**, not a production or candidate default.

### Why the simple gate missed W2
BACKTEST-013 showed W2 as a *window-level* high-range chop path. A rolling 96-bar (24h) efficiency filter is a *local* label. Most W2 entries still occurred on bars labeled TREND, so the failure was not concentrated in the small RANGE_CHOP subset this threshold catches.

## Interpretation Guardrails
- Gate did **not** help W2 under these thresholds → do not promote.
- Do **not** retune thresholds on the same 90-day sample as a “fix” — that is curve-fitting.
- Next diagnostics should either:
  1. define a **window/segment** regime label (not only 24h rolling), or
  2. study **which entry contexts inside W2** drive the short-edge collapse (score 75–84, EMA-UP, etc.) with a hold-out design.

## Known Limitations
- Classifier thresholds are research defaults from BACKTEST-013, not optimized.
- 90-day sample only; three 30-day windows.
- No external macro regime labels.
- Local RANGE_CHOP ≠ full 30-day W2 character.

## Recommended Decision
| Profile | Action |
|---|---|
| `v3_candidate` (ungated) | Keep as research baseline |
| `v3_candidate_regime_gated` | Keep as experimental API only; **not** promoted |
| Production defaults | Unchanged |

## Reproducibility
```bash
uv run python tools/run_backtest_014_regime_gate_validation.py
```
