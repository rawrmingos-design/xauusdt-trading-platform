# XAUUSDT Trading Platform

Production-grade foundation for XAUUSDT perpetual futures trading.

## Quick Start

```bash
# install
python -m pip install --upgrade uv
uv sync

# run smoke check
PYTHONPATH=src python -m xauusdt.cli

# tests
uv run pytest

# lint
uv run ruff check src tests
uv run ruff format src tests --check

# type check
uv run mypy src
```

## Docker

```bash
docker compose up --build
```

## Project Structure

```text
src/xauusdt/
  exchange/      # exchange adapters
  collectors/    # candle collectors (historical backfill, live WebSocket)
  data/          # market data ingestion and normalization
  features/      # feature engineering
  strategy/      # strategy definitions
  risk/          # risk controls
  execution/     # order management
  storage/       # persistence layer
  monitoring/    # metrics and alerts
```

## Environment

See `.env.example`.

## Constraints

- UTC-only timestamps
- No secrets in repo
- No LLM in runtime trading path
- Modular monolith, not microservices

## Historical Backfill

Download and persist historical OHLCV candles for XAUUSDT futures.

### CLI usage

```bash
# Backfill 5m candles for the last 7 days
PYTHONPATH=src python -m xauusdt.collectors.cli \
  --granularity 5m \
  --start-time 2025-01-01T00:00:00Z \
  --end-time 2025-01-08T00:00:00Z

# Dry run (download + validate only, no persistence)
PYTHONPATH=src python -m xauusdt.collectors.cli \
  --granularity 1H \
  --start-time 2025-01-01T00:00:00Z \
  --end-time 2025-01-02T00:00:00Z \
  --dry-run

# Custom database URL
PYTHONPATH=src python -m xauusdt.collectors.cli \
  --granularity 4H \
  --start-time 2025-01-01T00:00:00Z \
  --end-time 2025-01-02T00:00:00Z \
  --db-url "postgresql+asyncpg://user:pass@localhost/xauusdt"
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `--granularity` | Yes | `5m`, `15m`, `1H`, or `4H` |
| `--start-time` | Yes | Inclusive start time (ISO-8601) |
| `--end-time` | Yes | Exclusive end time (ISO-8601) |
| `--symbol` | No | Futures symbol (default: `XAU-USDT-SWAP`) |
| `--dry-run` | No | Download + validate only |
| `--db-url` | No | Database URL (default: SQLite in `xauusdt.db`) |
| `--output` | No | Write result JSON to file |

### Output

Result is printed as JSON to stdout:

```json
{
  "symbol": "XAU-USDT-SWAP",
  "granularity": "15m",
  "start_time": "2025-01-01T00:00:00+00:00",
  "end_time": "2025-01-02T00:00:00+00:00",
  "downloaded_count": 96,
  "stored_count": 96,
  "gap_count": 2,
  "gaps": [
    {"missing_open_time": "2025-01-01T08:15:00+00:00"},
    {"missing_open_time": "2025-01-01T08:30:00+00:00"}
  ],
  "dry_run": false,
  "status": "completed_with_gaps"
}
```

## Live WebSocket Candle Collector

Collect real-time candlestick data from Bitget Futures via WebSocket.

### How it works

1. Subscribe to Bitget Futures candlestick channel for XAUUSDT
2. Receive snapshot (initial state) + update (real-time changes)
3. Track in-progress candles separately from finalized candles
4. **Finalize only when a newer interval candle appears** — this is the key safety mechanism
5. Persist finalized candles through `CandleRepository.upsert_many()` idempotently

### CLI usage

```bash
# Run live collector (SQLite)
uv run xauusdt-collect --symbol XAU-USDT-SWAP \
  --granularities 5m,15m,1H,4H

# With PostgreSQL
uv run xauusdt-collect --db-url "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"

# Single granularity
uv run xauusdt-collect --granularities 5m --log-level DEBUG
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `--symbol` | `XAU-USDT-SWAP` | Futures symbol |
| `--granularities` | `5m,15m,1H,4H` | Comma-separated intervals |
| `--db-url` | `sqlite+aiosqlite:///xauusdt.db` | Database URL |
| `--log-level` | `INFO` | Logging level |

### Supported granularities

`5m`, `15m`, `1H`, `4H`

### Shutdown

Send `SIGINT` (Ctrl+C) or `SIGTERM` for graceful shutdown. The collector will disconnect cleanly from the WebSocket and close the database connection.

---

## Operational Runbook

### Live Collector Smoke Run

Run the live WebSocket collector on the VPS for a 6–24 hour validation period:

```bash
# Start collector in background, logging to file
nohup uv run xauusdt-collect \
  --symbol XAU-USDT-SWAP \
  --granularities 5m,15m,1H,4H \
  --db-url "$DATABASE_URL" \
  --log-level INFO \
  >> /var/log/xauusdt/collector.log 2>&1 &

# Monitor live logs
tail -f /var/log/xauusdt/collector.log
```

Expected behavior:
- Collector connects to Bitget WebSocket on startup
- Logs `Connected to WebSocket` on successful connect
- Logs `Candle snapshot received: 5m, 14:00` for each interval snapshot
- Logs `Persisted 1 candle(s)` when finalizing a candle
- Logs reconnect attempts with backoff on disconnect
- Gracefully shuts down on SIGTERM (clean disconnect)

### 6-Hour Validation

After running for 6 hours, validate continuity:

```bash
# Validate last 6 hours of 15m candles
uv run python tools/validate_candles.py \
  --symbol XAU-USDT-SWAP \
  --granularity 15m \
  --start-time 2026-07-14T06:00:00Z \
  --end-time 2026-07-14T12:00:00Z \
  --db-url "$DATABASE_URL" \
  --output json

# Validate all granularities
for GRAN in 5m 15m 1H 4H; do
  uv run python tools/validate_candles.py \
    --symbol XAU-USDT-SWAP \
    --granularity "$GRAN" \
    --start-time 2026-07-14T06:00:00Z \
    --end-time 2026-07-14T12:00:00Z \
    --db-url "$DATABASE_URL" \
    --output json
done
```

Expected: `status: "passed"` or `"warning"` (minor gaps acceptable during startup).
If `status: "failed"` with `duplicate_count > 0`, investigate duplicate persistence.

### Compare DB vs REST

Verify stored candles match REST historical data:

```bash
uv run python tools/compare_candles.py \
  --symbol XAU-USDT-SWAP \
  --granularity 15m \
  --start-time 2026-07-14T00:00:00Z \
  --end-time 2026-07-14T12:00:00Z \
  --db-url "$DATABASE_URL" \
  --tolerance 0.00000001 \
  --output json
```

Expected: `status: "passed"`, `matched == rest_count == db_count`, `mismatched == 0`.
If `missing_in_db > 0`, run backfill for the missing range.
If `mismatched > 0`, check for data corruption in storage.

### Recovering Gaps

When validation detects gaps after collector downtime:

```bash
# Run backfill for the gap range
uv run xauusdt-backfill \
  --symbol XAU-USDT-SWAP \
  --granularities 5m,15m,1H,4H \
  --days 1 \
  --dry-run          # First check what will change
  --db-url "$DATABASE_URL"

# Apply (remove --dry-run to persist)
uv run xauusdt-backfill \
  --symbol XAU-USDT-SWAP \
  --granularities 5m,15m,1H,4H \
  --days 1 \
  --db-url "$DATABASE_URL"
```

Backfill is idempotent — safe to run repeatedly. The upsert will not create duplicates.

### Inspecting Stored Candles

```bash
# Quick count of stored candles by granularity
uv run python -c "
import asyncio, os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    engine = create_async_engine(os.environ['DATABASE_URL'])
    async with engine.connect() as conn:
        result = await conn.execute(
            text('SELECT granularity, COUNT(*), MIN(open_time), MAX(open_time) '
                 'FROM candles WHERE symbol = :symbol GROUP BY granularity ORDER BY granularity')
        )
        for row in result:
            print(f'  {row[0]}: {row[1]} candles, {row[2]} → {row[3]}')
    await engine.dispose()

asyncio.run(main())
"
```

### Common Failure Modes

| Symptom | Likely Cause | Fix |
|---|---|---|
| Collector crashes on startup | Invalid DB URL or missing table | Verify `DATABASE_URL`, ensure tables created via alembic |
| No candles persist after connect | Granularity mismatch | Check `--granularities` matches Bitget supported values |
| Frequent reconnects (10+/min) | Network instability or Bitget rate limit | Check network, verify no excessive subscriptions |
| Duplicate candles in DB | Bug in finalization logic | Run `validate_candles.py`, file issue if duplicate_count > 0 |
| Gaps in validation report | Collector downtime or crash | Run backfill for missing range |
| REST comparison mismatches | Clock drift during storage | Check server time sync (`timedatectl status`) |

### Expected Limitations

- **No message ordering guarantees across reconnects**: During a reconnect, candles may arrive out of order. The `LiveCandleCollector` handles this via deduplication — duplicate open_times are only persisted once.
- **Single-connection WebSocket**: The collector maintains one WebSocket connection. If Bitget drops the connection, reconnect happens with exponential backoff (1–30 seconds).
- **Public API only**: Only public market data channels are supported. Private authenticated WebSocket channels are not implemented.
- **No alerting**: The collector does not send alerts on disconnect or data gaps. Use `validate_candles.py` in cron for automated gap detection.
- **No backfill in validation tools**: `validate_candles.py` and `compare_candles.py` are read-only. Gaps are reported, not fixed.

