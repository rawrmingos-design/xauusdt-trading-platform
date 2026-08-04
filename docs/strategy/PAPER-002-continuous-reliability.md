# PROJECT-PAPER-002 — Continuous Live Polling Reliability Validation

**Status**: PASSED (engineering reliability test)
**Date**: 2026-08-04
**Repository**: rawrmingos-design/xauusdt-trading-platform
**Branch**: feat/project-paper-002-continuous-reliability

> This is an engineering reliability validation, NOT a strategy validation.
> No real orders were placed. No strategy parameters were changed.
> Forward candles observed are quarantine-listed (see below) and were NOT
> used for any redesign or parameter change.

## Executive Summary

The continuous shadow and paper harness was validated end-to-end against live
OKX market data (`XAU-USDT-SWAP`, 15m candles) and deterministic fixtures:

| Requirement | Result |
|---|---|
| Continuous shadow mode processes finalized candles | ✅ |
| Continuous paper mode processes finalized candles | ✅ |
| Duplicate candle processing produces no duplicate records | ✅ |
| Restart with same run ID resumes safely | ✅ |
| Restart with new run ID creates independent run | ✅ |
| Open position state survives restart | ✅ |
| Stale-candle guard blocks new entries | ✅ |
| Candle-gap guard blocks new entries | ✅ |
| SQLite migrations idempotent | ✅ |
| Daily reports generated | ✅ |
| No real order path in smoke run | ✅ |
| pytest / ruff / format / mypy | ✅ |

## What Changed

The PAPER-001 harness persisted a complete run per batch with `INSERT OR
REPLACE`, and the continuous loop reset harness state every poll cycle. This
meant:

1. Open positions and equity counters were lost between cycles and restarts.
2. `save_run` replaced the whole run row every batch (slow, racy).
3. No mechanism existed to restore an open position after a process restart.

PAPER-002 adds continuous-state persistence:

- **`paper_positions` table** (SQLite): live state row per run — open position
  (side, entry, qty, SL/TP, partial state, excursions) + equity counters
  (balance, peak balance, max drawdown, candles processed, last processed
  candle). Upserted every batch; `side=''` when flat.
- **`PaperHarness.harness_state() / restore_state()`**: serialize and
  reconstruct live state (position + accounting).
- **`PaperHarness.process_candle_continuous()` + `drain_batch()`**: process
  candles incrementally, drain only new records per batch.
- **`PaperRunner.run_loop()`**: restore state on startup, process only fresh
  candles, append records, persist state + run meta every cycle.
- **`PaperStore.save_position() / get_position() / ensure_run() /
  save_run_meta() / append_records()`**: idempotent continuous persistence.
- **`PaperRunner.build_result_from_store()`**: reconstruct a full result from
  the DB to generate daily reports for continuous runs.
- Rejection reason `continuity_guard` recorded on signals blocked by the
  stale/gap guard.

## Test Evidence (automated)

`tests/unit/test_paper_continuous.py` — 10 deterministic tests, no network:

| Test | Verifies |
|---|---|
| `test_restore_open_position_roundtrip_recreates_identical_position` | position state round-trips through serialization |
| `test_restore_state_with_no_position_only_restores_equity` | flat-state restore keeps accounting |
| `test_runner_deduplicates_candles_across_restart` | same run ID: no reprocess, no duplicates |
| `test_runner_new_run_id_is_independent` | new run ID = clean slate |
| `test_runner_position_restored_after_restart` | open LONG survives restart with side/price intact |
| `test_runner_clean_partial_tp_state_roundtrips` | partial-TP + break-even state survives |
| `test_stale_candle_blocks_new_entry` | stale candle blocks entry |
| `test_gap_candle_blocks_new_entry` | gap candle blocks entry |
| `test_guards_record_rejection_reason` | `continuity_guard` reason recorded |
| `test_daily_reports_generated_from_continuous_run` | JSON+MD report from continuous DB |

Full suite: **257 passed, 16 skipped**. `ruff check` clean, `ruff format
--check` clean, `mypy src` clean (45 files).

## Live Smoke Evidence

### Shadow continuous

```
xauusdt-paper shadow --db ~/.hermes/xauusdt_paper/paper_runs.db \
  --run-id paper002-shadow-live --max-cycles 3 --poll-interval 5
```

- Fetch OKX `history-candles` 200 OK
- 5 candles processed, latest `2026-08-04T08:15:00+00:00`
- Subsequent cycles returned no fresh candles → skipped (no reprocess)
- Restart same run ID: `Restored open position side= qty=0` → signals stayed
  **5**, distinct **5**, `candles_processed` **5** — zero duplicates

### Paper continuous

```
xauusdt-paper paper --db ~/.hermes/xauusdt_paper/paper_runs.db \
  --run-id paper002-paper-live --max-cycles 2 --poll-interval 5
```

- 5 signals, 0 orders, no position opened (v3_candidate did not trigger an
  entry in the sampled window — expected, not a failure)
- Restart same run ID: signals **5**, distinct **5**, orders **0** — no dup

### Crash-style restart (SIGKILL)

```
xauusdt-paper paper --db /tmp/crash_paper.db --run-id crash-test
  --max-cycles 100 --poll-interval 3
kill -9 <pid>
```

- `PRAGMA integrity_check` → **ok**
- After restart: run row 1, signals 5 (distinct 5), positions 1 — no double
  positions, state readable, resumes cleanly

## Guard Demonstrations (deterministic fixtures)

| Guard | Fixture | Result |
|---|---|---|
| Stale candle | 2 normal candles then a candle 1h late | entry blocked, `continuity_guard` reason recorded |
| Candle gap | 2 normal candles then a candle 6h later | entry blocked, `continuity_guard` reason recorded |

## Forward-Data Quarantine

Candles observed during this smoke run (post 2026-07-15):

- `2026-08-04T08:00:00+00:00` .. `2026-08-04T08:15:00+00:00` (shadow)
- `2026-08-04T08:00:00+00:00` .. `2026-08-04T08:15:00+00:00` (paper)
- `2026-08-04T08:00:00+00:00` .. `2026-08-04T08:15:00+00:00` (crash-test)

Status: **forward observation, engineering-only, not used for redesign, not
used for promotion.** No parameter changes were made from these candles.

## Run Metadata

| Field | Shadow | Paper |
|---|---|---|
| Run ID | `paper002-shadow-live` | `paper002-paper-live` |
| Config | `make_v3_candidate_config()` | `make_v3_candidate_config()` |
| Strategy version | v3_candidate | v3_candidate |
| Commit SHA | see PR | see PR |
| Last processed candle | `2026-08-04T08:15:00+00:00` | `2026-08-04T08:15:00+00:00` |
| DB path | `~/.hermes/xauusdt_paper/paper_runs.db` | same |
| Process mode | shadow (no positions) | paper (simulated) |

## Sample Daily Report

See `docs/reports/paper002_continuous_*.json` / `.md` in this PR. Example
generated from continuous-run data via `PaperRunner.build_result_from_store`
+ `write_daily_report`.

## Known Limitations

1. Live smoke window was short (minutes) — no position happened to open during
   the sample; position recovery is proven via deterministic fixtures and the
   crash-restart test, not yet via a live open position. The forward OOS run
   (PROJECT-FORWARD-OOS) will exercise this.
2. `save_run` (used by `--once` mode) and `append_records` (continuous) both
   exist; `--once` still writes full runs via `save_run`. Both are idempotent.
3. Poll interval 120s default: a 15m candle is finalized before the next poll
   normally, but a poll can only see candles OKX serves; no backfill logic
   beyond fetch-limit=5. A long downtime > 5 candles could miss candles; the
   gap guard then blocks entries until continuity resumes.
4. SQLite single-writer: only one paper process per DB file is supported.
5. `xauusdt-paper` continuous mode uses the validated OKX REST collector only
   (no websocket) — latency of new-candle detection is bounded by
   `--poll-interval`.

## Recommended Next Steps

1. **PROJECT-RISK-001** — deterministic risk + position sizing engine:
   fixed fractional sizing (0.25% risk/trade), daily loss limit (1.0%),
   weekly drawdown (2.5%), consecutive-loss cooldown (3), one-position guard,
   stale/gap execution block, manual kill switch, persistent risk state,
   restart-safe counters.
2. **PROJECT-MONITORING-001** — health metrics + Telegram alerts.
3. **Forward OOS checkpoint** (min 60d, from 2026-07-16): run fixed configs on
   forward data, audit gaps, log results. Checkpoint 2026-09-13.

## Commands (Runbook)

```bash
# fresh smoke DB
rm -f ~/.hermes/xauusdt_paper/paper_runs.db

# shadow continuous (poll every 120s, default)
uv run xauusdt-paper shadow --run-id paper002-shadow-live

# paper continuous
uv run xauusdt-paper paper --run-id paper002-paper-live

# bounded smoke (N cycles) for CI / quick checks
uv run xauusdt-paper paper --run-id smoke --max-cycles 3 --poll-interval 5

# restart with same run ID (idempotent resume)
uv run xauusdt-paper paper --run-id paper002-paper-live

# restart with new run ID (independent run)
uv run xauusdt-paper paper --run-id paper002-paper-live-2
```
