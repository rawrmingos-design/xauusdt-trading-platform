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
| `--symbol` | No | Futures symbol (default: `XAUUSDT_UMCBL`) |
| `--dry-run` | No | Download + validate only |
| `--db-url` | No | Database URL (default: SQLite in `xauusdt.db`) |
| `--output` | No | Write result JSON to file |

### Output

Result is printed as JSON to stdout:

```json
{
  "symbol": "XAUUSDT_UMCBL",
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
uv run xauusdt-collect --symbol XAUUSDT_UMCBL \
  --granularities 5m,15m,1H,4H

# With PostgreSQL
uv run xauusdt-collect --db-url "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"

# Single granularity
uv run xauusdt-collect --granularities 5m --log-level DEBUG
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `--symbol` | `XAUUSDT_UMCBL` | Futures symbol |
| `--granularities` | `5m,15m,1H,4H` | Comma-separated intervals |
| `--db-url` | `sqlite+aiosqlite:///xauusdt.db` | Database URL |
| `--log-level` | `INFO` | Logging level |

### Supported granularities

`5m`, `15m`, `1H`, `4H`

### Shutdown

Send `SIGINT` (Ctrl+C) or `SIGTERM` for graceful shutdown. The collector will disconnect cleanly from the WebSocket and close the database connection.

