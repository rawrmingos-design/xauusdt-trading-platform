# PROJECT-FORWARD-OOS-001 — Forward OOS Evaluation Specification

**Status**: FROZEN (pre-registered before the 60-day checkpoint)
**Repository**: rawrmingos-design/xauusdt-trading-platform
**Base branch**: `main`
**Frozen at**: 2026-08-05 (before 2026-09-13T00:00:00Z checkpoint)

## 1. Purpose

Evaluate the frozen research baseline `make_v3_candidate_config()` on
**untouched forward-out-of-sample** data collected by the continuous paper
runtime. The evaluation methodology, interpretation rules, and decision
rules are committed BEFORE any forward performance number is produced, so
no post-hoc threshold tuning is possible.

## 2. Runtime context

| Item | Value |
|---|---|
| Host | `mail.istanatopup.com` |
| run_id | `forward-paper-v3-candidate-20260716` |
| Forward start | `2026-07-16T00:00:00Z` |
| Paper DB | `/var/lib/xauusdt/paper_runs.db` |
| Strategy profile | `make_v3_candidate_config()` (frozen reference research baseline) |
| Granularity | 15m |
| Real orders | `false` (paper only) |

## 3. Checkpoints (all UTC)

| Checkpoint | Datetime | Content |
|---|---|---|
| operations_30d | 2026-08-14T00:00:00Z | operational/data-quality review |
| preliminary_60d | 2026-09-13T00:00:00Z | first performance evaluation |
| stronger_90d | 2026-10-13T00:00:00Z | second performance evaluation |

## 4. Data sources (stored only)

- **Forward candles**: `paper_candles` table in the paper DB, written by the
  runtime every poll cycle (PROJECT-FORWARD-OOS-001 runtime change). The
  offline replay reads ONLY these stored candles. No live OKX call during
  evaluation.
- **Paper records**: `paper_signals`, `paper_orders`, `paper_exits`,
  `paper_positions` for the fixed run_id.
- **Deployment metadata**: `deployment.json` beside the DB.

## 5. Anti-peeking controls

1. `evaluate` refuses execution before `2026-09-13T00:00:00Z` (exit 1).
2. No force / bypass / debug / env-var / alternate-date option exists.
3. Wall clock is UTC-aware (`datetime.now(UTC)`).
4. `status` exposes operational & data-quality info ONLY.
5. Committed reports before the checkpoint contain NO performance metrics.
6. Tests use synthetic fixtures or already-consumed historical data only.
7. Forward data may not be used to redesign strategy/features/exits/risk.

### Allowed pre-checkpoint outputs (`status`)

forward start, latest candle, expected/actual count, coverage %, gap count,
duplicate count, DB integrity, heartbeat, service, backup freshness,
deployment commit SHA, config hash, evaluation locked-until.

### Forbidden pre-checkpoint outputs

net PnL, expectancy, profit factor, win rate, drawdown, avg realized R,
long vs short, exit distribution, strategy comparison, counterfactuals,
trade-by-trade outcomes.

## 6. Evaluation method

1. `status` — operational health & data-quality audit.
2. At checkpoint: `evaluate --checkpoint 60d|90d`.
3. Gate: UTC now >= checkpoint end bound.
4. Validate: run exists, config_hash present.
5. Load stored candles in `[forward_start, checkpoint_end)`.
6. Audit: expected count vs actual, gaps, duplicates, coverage, fingerprint.
   Refuse if incomplete (`DataQualityError`-style, exit 2).
7. Replay: `ConfluenceBacktestEngine` + `make_v3_candidate_config()`,
   `BacktestConfig(initial_balance=10_000, fee_rate=0.0006, slippage_bps=5.0)`.
8. Compute metrics (entry-grouped parent trades).
9. Paper-vs-replay parity.
10. Write frozen report JSON + Markdown with checkpoint label & UTC timestamp.

### 6.1 Paper-vs-replay parity

| Signal parity | entry parity | exit parity | quantity parity | realized-PnL parity |
|---|---|---|---|---|
| paper_signals vs replay_signals | paper_entries vs replay_entries | paper_exits vs replay_exits | per-trade qty | per-parent PnL |

### 6.2 Reported metrics (checkpoint only)

trade count, expectancy R, profit factor, win rate, net PnL, max drawdown,
avg realized R, exit-reason distribution, break-even count; long/short
separately.

### 6.3 Chronological sub-windows

- 60-day period split into two chronological 30-day halves.
- 90-day period split into three chronological 30-day thirds.

## 7. Realized R convention

Realized R per parent trade = `parent_pnl / (entry_price * quantity * sl_distance)`.
Multi-leg partial exits are grouped by `entry_candle_time` into one parent
trade BEFORE averaging R (avoids compounding-decay distortion from dollar PnL).

## 8. Pre-registered interpretation rules

### Promising
- v3_candidate expectancy positive after fees and slippage
- profit factor > 1.0
- results not entirely carried by one chronological sub-window
- paper vs replay parity acceptable
- sufficient trade count

### Inconclusive
- expectancy near zero, PF ≈ 1.0, insufficient trades, sub-windows
  materially disagree, or execution-parity problems prevent a clean verdict

### Negative
- expectancy clearly negative, PF < 1.0, parity valid, losses not
  attributable to missing data or execution defects

## 9. Decision rules

| Outcome at 60d | Action |
|---|---|
| Promising | continue unchanged config to 90d |
| Inconclusive | continue unchanged config to 90d |
| Negative | NO re-fitting on forward sample |

- No profile promoted to production from the 60-day result.
- No real trading enabled from this task.
- Future redesign requires a separately declared discovery dataset and a new
  untouched validation plan.

## 10. Evaluation profiles

| Profile | Factory | Role |
|---|---|---|
| primary | `make_v3_candidate_config()` | only profile eligible for primary verdict |
| secondary | `make_v3_candidate_s7b_config()` | archived research context only, promotion not allowed |

## 11. Determinism

Same stored candles -> same audit -> same fingerprint -> same manifest.
Fingerprint = SHA-256 over sorted OHLCV rows. Report path embeds checkpoint
label + UTC execution timestamp.

## 12. Commands

```bash
# status (any time before checkpoint)
uv run python tools/run_forward_oos_001.py status \
  --run-id forward-paper-v3-candidate-20260716 \
  --db /var/lib/xauusdt/paper_runs.db

# evaluate at 60-day checkpoint
uv run python tools/run_forward_oos_001.py evaluate \
  --checkpoint 60d \
  --run-id forward-paper-v3-candidate-20260716 \
  --db /var/lib/xauusdt/paper_runs.db

# evaluate at 90-day checkpoint
uv run python tools/run_forward_oos_001.py evaluate \
  --checkpoint 90d \
  --run-id forward-paper-v3-candidate-20260716 \
  --db /var/lib/xauusdt/paper_runs.db
```

## 13. Out of scope

Strategy logic/config changes, new filters, risk threshold changes, feature
research, parameter optimization, real OKX orders, private OKX credentials,
profile promotion, production strategy selection, LLM-based evaluation
decisions.
