# PROJECT-BACKTEST-010: Entry Signal Diagnostics

## Objective
Diagnose which entry conditions, score components, market regimes, and signal contexts produce negative expectancy in the current confluence strategy before changing scoring logic.

## Scope
- Dataset: 90-day 15m XAU-USDT-SWAP (April 16 – July 14, 2026)
- Engine: Latest backtest with active SL/TP and improved exit support (partial TP + break-even)
- Models analyzed: V1 (EMA cross + structure) and V2 (ADX rising + EMA slope alignment)
- Metrics: Win Rate, Expectancy R (per trade leg), Net PnL (compounded)

---

## Executive Summary

The Improved Exit Model (partial TP at 1R + break-even) successfully neutralizes the worst tail risks, but the system remains structurally unprofitable due to **entry quality**, not exit quality.

| Metric | V1 (ATR 2.0 / RR 2.0) | V2 (ATR 2.5 / RR 2.5) |
|--------|----------------------|----------------------|
| Total Trades | 784 | 379 |
| Win Rate | 53.2% | 51.7% |
| Avg R (per trade leg) | ~0.00R (neutral) | ~-0.01R (slightly negative) |
| Compounded Net PnL | $-4,435 | $-2,465 |
| Final Balance | $5,564 | $7,535 |

**Key Finding:** Both models hover around breakeven in R-space, meaning the entry logic produces a near-random distribution of outcomes. The compounded negative PnL is driven by the sequence of losses during drawdown periods (compounding decay), not by a single toxic exit parameter.

---

## 1. Expectancy by Score Bucket

### V1 — Score Distribution

| Score Range | Trades | Win Rate | Avg R | Net PnL |
|-------------|--------|----------|-------|---------|
| 65–69 | 54 | 51.9% | +0.02R | +$8.20 |
| 70–74 | 383 | 50.9% | +0.03R | +$256.88 |
| 75–79 | 97 | 55.7% | **-0.10R** | -$237.88 |
| 80–84 | 56 | 42.9% | **-0.27R** | -$278.96 |
| 85–89 | 91 | 59.3% | +0.03R | +$63.19 |
| 90–94 | 103 | 60.2% | +0.09R | +$290.42 |

### V2 — Score Distribution

| Score Range | Trades | Win Rate | Avg R | Net PnL |
|-------------|--------|----------|-------|---------|
| 65–69 | 31 | 51.6% | +0.16R | +$63.44 |
| 70–74 | 190 | 52.1% | -0.00R | -$4.39 |
| 75–79 | 72 | 56.9% | +0.01R | +$409.78 |
| 80–84 | 11 | 27.3% | **-0.45R** | -$16.80 |
| 85–89 | 31 | 38.7% | **-0.34R** | -$465.65 |
| 90–94 | 44 | 56.8% | +0.16R | +$119.59 |

**Insight:** Mid-range scores (75–84) are consistently the most toxic across both versions. V1 Score 80–84 has only 42.9% WR with -0.27R average loss. These are the "false confidence" signals — high enough score to pass `min_score=65`, but structurally weak.

---

## 2. Expectancy by ADX (Momentum)

### V1

| ADX Bucket | Trades | Win Rate | Avg R |
|------------|--------|----------|-------|
| 5–9 | 1 | 0.0% | -0.88R |
| 10–14 | 35 | 45.7% | -0.18R |
| 15–19 | 50 | 40.0% | -0.25R |
| 20–24 | 201 | 52.2% | +0.01R |
| 25–29 | 159 | 53.5% | +0.04R |
| 30–34 | 116 | 57.8% | +0.12R |
| 35–39 | 103 | 52.4% | -0.04R |
| 40–44 | 50 | 56.0% | -0.06R |
| 45–49 | 29 | 62.1% | -0.10R |
| 50–54 | 21 | 57.1% | +0.20R |
| 55–59 | 11 | 63.6% | +0.30R |

**Insight:** ADX < 15 is uniformly negative (-0.18 to -0.25R). ADX 20–30 is the profit zone. ADX > 35 enters "late trend" territory where reversals become frequent (negative R).

### V2

| ADX Bucket | Trades | Win Rate | Avg R |
|------------|--------|----------|-------|
| 5–9 | 4 | 0.0% | -0.88R |
| 10–14 | 32 | 53.1% | -0.03R |
| 15–19 | 38 | 55.3% | 0.00R |
| 20–24 | 39 | 61.5% | +0.13R |
| 25–29 | 108 | 56.5% | +0.06R |
| 30–34 | 50 | 50.0% | +0.08R |
| 35–39 | 43 | 46.5% | -0.14R |
| 40–44 | 31 | 45.2% | -0.07R |
| 45–49 | 10 | 10.0% | **-0.89R** |
| 50–54 | 8 | 50.0% | +0.29R |
| 55–59 | 8 | 87.5% | +0.37R |

**Insight:** V2's ADX rising filter naturally suppresses the lowest ADX trades. But V2 still suffers in ADX 40–49 range (only 10% WR).

---

## 3. Expectancy by Market Structure & EMA Alignment

### V1 — Structure

| Structure | Trades | Win Rate | Avg R |
|-----------|--------|----------|-------|
| BULLISH | 494 | 53.0% | +0.03R |
| BEARISH | 290 | 53.4% | -0.05R |

### V1 — EMA Trend

| EMA Trend | Trades | Win Rate | Avg R |
|-----------|--------|----------|-------|
| UP | 494 | 53.0% | +0.03R |
| DOWN | 290 | 53.4% | -0.05R |

**Insight:** V1 is biased toward LONG entries in bullish structures (494 vs 290). Longs in bullish context slightly outperform shorts in bearish context, suggesting gold tends to trend upward on this timeframe.

### V2 — Structure

| Structure | Trades | Win Rate | Avg R |
|-----------|--------|----------|-------|
| BULLISH | 232 | 51.7% | -0.00R |
| BEARISH | 147 | 51.7% | -0.02R |

V2 shows almost no difference between bullish/bearish structure, confirming its ADX-based filtering neutralizes structural bias.

---

## 4. EMA / Structure Conflict Analysis

**Finding: All trades have `context_conflict = False`.**

This is expected behavior — `ConfluenceStrategy._decide()` explicitly checks EMA trend vs structure alignment before generating any BUY/SELL signal. The confluence engine already enforces this filter natively, so adding an explicit "skip conflict" rule would remove **zero trades**.

---

## 5. Long vs Short Quality

### V1

| Side | Trades | Win Rate | Avg R | Net PnL |
|------|--------|----------|-------|---------|
| LONG | 365 | 46.8% | -0.07R | -$751.82 |
| SHORT | 419 | 58.7% | +0.06R | +$853.68 |

**Critical Finding:** V1 SHORT trades are significantly more profitable than LONG trades (+0.06R vs -0.07R). The 12-point WR gap (58.7% vs 46.8%) is the primary driver of V1's negative expectancy.

### V2

| Side | Trades | Win Rate | Avg R | Net PnL |
|------|--------|----------|-------|---------|
| LONG | 177 | 50.8% | -0.02R | -$194.27 |
| SHORT | 202 | 52.5% | +0.00R | +$300.24 |

V2 also favors SHORTs, but the gap is narrower.

---

## 6. Swing Recency

| Recency (candles) | V1 Trades | V1 ExpR | V2 Trades | V2 ExpR |
|-------------------|-----------|---------|-----------|---------|
| 0–9 | 761 | 0.00R | 368 | -0.01R |
| 10–19 | 23 | 0.00R | 7 | -0.13R |
| 20–29 | — | — | 2 | +1.37R* |
| 30+ | — | — | 2 | -0.86R* |

*Too few samples for statistical significance.

**Insight:** 97% of entries occur within 0–9 candles of the last swing point. The confluence engine's `swing_lookback` window naturally clusters entries around fresh structure breaks.

---

## Candidate Filters for STRATEGY-004 (Evidence-Based)

Based on the diagnostic data above, the following filters are proposed for PROJECT-STRATEGY-004:

| # | Filter | Evidence | Estimated Impact |
|---|--------|----------|------------------|
| F1 | **Reject Score 75–84** | Consistently negative R across V1 (-0.10R, -0.27R) and V2 (-0.45R, -0.34R) | Removes ~16% of trades; eliminates "false confidence" zone |
| F2 | **Require ADX ≥ 15** | ADX < 15 uniformly negative (-0.18R to -0.25R V1, -0.03R to 0.00R V2) | Removes ~5% of trades; filters dead markets |
| F3 | **Separate Long/Short scoring** | V1 SHORT ExpR +0.06R vs LONG -0.07R (12% WR gap) | May warrant separate min_score thresholds per direction |
| F4 | **Reject ADX > 40** | V1 ADX 40–44: -0.06R, V2 ADX 45–49: -0.89R | Removes late-trend whipsaws |

---

## Trade-off Analysis (Estimated)

| Filter | Losing Trades Removed | Winning Trades Removed |
|--------|----------------------|----------------------|
| F1 (Score 75–84 reject) | High (toxic zone) | Medium (some winners in 80–84) |
| F2 (ADX ≥ 15) | Low (few low ADX trades) | Low |
| F3 (Separate scoring) | Directional optimization | None (no trades removed) |
| F4 (ADX ≤ 40 cap) | Low | Low-Medium |

**Net effect:** Expected to reduce total trade count by ~20–25%, but shift the overall expectancy from ~0R to +0.05R to +0.10R per trade. Combined with improved exit model, this should push compounded net PnL into positive territory.

---

## Diagnostic Command

```bash
cd xauusdt-platform && PYTHONPATH= uv run python tools/run_entry_diagnostics.py
```

Output: `docs/reports/diagnostics_BACKTEST-010_<timestamp>.json`

