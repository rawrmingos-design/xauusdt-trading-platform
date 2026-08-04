# PROJECT-DATA-010: Extended OKX Dataset + Freeze

## Status
**Data foundation only.** No strategy changes. No edge claims. No re-fit.

## Goal
1. Extend `XAU-USDT-SWAP` historical candles beyond the prior ~180d store (target **365d**, best-effort if OKX shorter).
2. Audit gaps / duplicates / continuity + REST-vs-DB spot checks.
3. Freeze **dataset versions** as reproducible manifests for BT-018 / BT-019.

## Reproduce backfill

```bash
# Preferred one-shot (extend + audit + freeze manifests)
uv run python tools/run_data_010_extend.py --days 365 --granularity 15m,1H

# Or low-level backfill only (idempotent upsert)
uv run python tools/run_backfill.py --symbol XAU-USDT-SWAP --granularity 15m --days 365
uv run python tools/run_backfill.py --symbol XAU-USDT-SWAP --granularity 1H --days 365
```

Notes:
- Backfill is **resumable/idempotent** via `CandleRepository.upsert_many` (ON CONFLICT).
- Existing 180d rows are **not deleted**; extension upserts older history into the same table.
- `--skip-backfill` re-audits and rewrites manifests from whatever is already in Postgres.

## Dataset versioning

Manifests live under `docs/datasets/*.json`.

| Tool | Purpose |
|---|---|
| `tools/list_dataset_versions.py` | List frozen versions |
| `tools/select_dataset.py --version <id>` | Write `docs/datasets/SELECTED.json` pointer |
| `tools/select_dataset.py --version <id> --print-env` | Shell exports for backtest runners |

Each manifest records: `version_id`, symbol, granularity, start/end, candle_count, gap_count, duplicate_count, continuity, source, created_at, fingerprint, period labels, quality checks.

**Logical freeze, not a DB clone.** All versions share the Postgres `candles` table; a version is a documented time window + quality snapshot + fingerprint.

### How future backtests should reference a version

1. `uv run python tools/select_dataset.py --version ds_xau_15m_365d_v1`
2. Read `docs/datasets/SELECTED.json` (or the version manifest) for `start_time` / `end_time`.
3. Load candles with `CandleRepository.query_by_range` and **filter** to that window.
4. Cite `version_id` + `fingerprint` in the backtest report JSON/MD.

Do **not** silently use “whatever is in the DB today” without a version id.

## Period labels

| Period | Range (UTC) | Role |
|---|---|---|
| Discovery | 2026-04-16 → 2026-07-15 | Contaminated design sample (BT-011..016) |
| Pre-discovery OOS | 2026-01-16 → 2026-04-16 | BT-017 true OOS |
| Extended pre-OOS | available_start → 2026-01-16 | Extra multi-regime history for BT-018 |
| Forward OOS candidate | 2026-07-15 → … | BT-019; **not** edge verdict until 30–60d |

## Explicit non-claims
- Extending history does **not** validate edge.
- Do not re-fit `v3_candidate` / s7* on the new span in this task.
- Do not promote production defaults from DATA-010.

## Next
1. **PROJECT-BACKTEST-018** — multi-regime OOS, fixed configs, extended dataset.
2. **PROJECT-BACKTEST-019** — forward OOS runner (smoke until enough post-discovery days).
