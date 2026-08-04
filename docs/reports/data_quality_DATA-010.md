# PROJECT-DATA-010: Extended Dataset + Freeze

**Generated**: 2026-08-04T05:38:52.520499+00:00
**Symbol**: `XAU-USDT-SWAP`
**Target**: 365d ending 2026-07-15 (best-effort if OKX shorter)

## Status
Data foundation only. **No strategy changes. No edge claims.**

## Backfill commands
```bash
uv run python tools/run_backfill.py --symbol XAU-USDT-SWAP --granularity 15m --days 365
uv run python tools/run_data_010_extend.py --days 365
```

## Available range & quality

| Granularity | Count | First | Last | Gaps | Dupes | Continuity | Coverage |
|---|---:|---|---|---:|---:|---|---:|
| 15m | 35040 | 2025-07-15T00:00:00+00:00 | 2026-07-14T23:45:00+00:00 | 0 | 0 | PASS | 100.0% |
| 1H | 8760 | 2025-07-15T00:00:00+00:00 | 2026-07-14T23:00:00+00:00 | 0 | 0 | PASS | 100.0% |

## REST-vs-DB spot checks

### 15m
- `2025-07-16T00:00:00+00:00`: **pass** (okx=100, matched=96, mismatches=0)
- `2026-01-13T11:52:30+00:00`: **pass** (okx=100, matched=100, mismatches=0)
- `2026-07-14T17:45:00+00:00`: **pass** (okx=100, matched=100, mismatches=0)

### 1H
- `2025-07-16T00:00:00+00:00`: **pass** (okx=100, matched=24, mismatches=0)
- `2026-01-13T11:30:00+00:00`: **pass** (okx=100, matched=100, mismatches=0)
- `2026-07-14T17:00:00+00:00`: **pass** (okx=100, matched=100, mismatches=0)

## Frozen manifests

- `15m` → `docs/datasets/ds_xau_15m_365d_v1.json`
- `1H` → `docs/datasets/ds_xauusdtswap_1H_365d_v1.json`
- `15m_180d` → `docs/datasets/ds_xau_15m_180d_v1.json`

## Period labels (for BT-018 / BT-019)

| Period | Range | Role |
|---|---|---|
| Discovery | 2026-04-16 → 2026-07-15 | Contaminated design sample |
| Pre-discovery OOS | 2026-01-16 → 2026-04-16 | BT-017 true OOS |
| Extended pre-OOS | available_start → 2026-01-16 | Extra multi-regime history |
| Forward OOS candidate | 2026-07-15 → … | BT-019; wait 30–60d for verdict |

## 180d integrity
Extended backfill uses upsert — existing 180d rows are not deleted. Pre-extension 180d audit snapshot is stored in the report JSON.

## Dataset tooling
```bash
uv run python tools/list_dataset_versions.py
uv run python tools/select_dataset.py --version ds_xau_15m_365d_v1
```

## Known limitations

- OKX public history-candles limited to 100 per request; pagination required.
- OKX may not retain full 365d for every instrument/granularity.
- Dataset versions are logical freezes (manifest + DB range), not separate DB clones.
- Forward post-discovery window still short (~19d as of early Aug 2026) — not for edge verdict.
- No strategy re-fit or promotion from this data task.

## Recommended next steps

- PROJECT-BACKTEST-018: multi-regime OOS on extended dataset, fixed v3_candidate vs s7b, no re-fit.
- PROJECT-BACKTEST-019: forward OOS harness post-discovery (smoke only until 30-60d).
- Keep collecting live 15m candles so forward window grows.

JSON report: `docs/reports/data_quality_DATA-010.json`

