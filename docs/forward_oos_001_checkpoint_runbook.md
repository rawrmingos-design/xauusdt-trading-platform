# PROJECT-FORWARD-OOS-001 — Checkpoint Runbook

## Before a checkpoint

1. `status` must show `Evaluation: LOCKED until 2026-09-13T00:00:00Z`.
2. Confirm heartbeat healthy, DB integrity `ok`, backup current.
3. Do NOT run `evaluate` before a checkpoint — it refuses (exit 1).

## At the 60-day checkpoint (2026-09-13T00:00:00Z)

```bash
cd ~/xauusdt-platform
uv run python tools/run_forward_oos_001.py evaluate \
  --checkpoint 60d \
  --run-id forward-paper-v3-candidate-20260716 \
  --db /var/lib/xauusdt/paper_runs.db \
  --out-dir docs/reports
```

The evaluator:
1. Verifies wall clock >= checkpoint (else refuses).
2. Audits dataset (coverage, gaps, dupes, fingerprint). Refuses incomplete
   data (exit 2) — do NOT bypass; fix data via ops then re-run.
3. Replays v3_candidate offline on stored forward candles.
4. Reports metrics (parent-grouped R, PF, win rate, sub-windows) and
   paper-vs-replay parity.
5. Writes `<out-dir>/forward_oos_60d_<UTC>.json`.

Report is the ONLY artifact that may contain performance numbers. Commit the
report + manifest. Do not commit performance numbers elsewhere pre-checkpoint.

## At the 90-day checkpoint (2026-10-13T00:00:00Z)

Same procedure with `--checkpoint 90d`.

## After a negative 60-day result

- Do NOT re-fit on the forward sample.
- Do NOT change config, filters, exits, risk from trade results.
- Collecting the full 90-day sample is the designed path; the 60-day result
  is preliminary only. Continue unchanged to 90d (per decision rules).

## Operational maintenance during the freeze

Allowed: monitor heartbeat, audit gaps/dupes, test backup/restore, check
disk, restart service, DB integrity, fix operational bugs via PR.
Forbidden: see expectancy while locked, see long/short while locked, change
filters/scoring/exits/risk based on trades, compare profiles to pick a winner.

## Validation & promotion

- No profile promoted from the 60-day result.
- Promotion (if any) requires the full evaluation + a separately declared,
  untouched validation plan. This task enables NO real trading.