# XAUUSDT Live Candle Collector — Smoke Test Report

**Date:** YYYY-MM-DD  
**Environment:** VPS (specify OS, region, specs)  
**Database:** PostgreSQL 16  
**Duration:** 6–24 hours  

---

## Collector Configuration

```bash
uv run xauusdt-collect \
  --symbol XAUUSDT_UMCBL \
  --granularities 5m,15m \
  --db-url "$DATABASE_URL" \
  --log-level INFO
```

**Database URL:** `postgresql+asyncpg://xauusdt:***@<HOST>:5432/xauusdt`  
**Granularities:** 5m, 15m  
**Start time (UTC):** YYYY-MM-DDTHH:MM:SSZ  
**End time (UTC):** YYYY-MM-DDTHH:MM:SSZ  
**Runtime duration:** HH:MM:SS  

---

## Validation Results

### 5m Candles

```bash
uv run python tools/validate_candles.py \
  --symbol XAUUSDT_UMCBL \
  --granularity 5m \
  --start-time YYYY-MM-DDTHH:MM:SSZ \
  --end-time YYYY-MM-DDTHH:MM:SSZ \
  --db-url "$DATABASE_URL" \
  --output json
```

**Output:**
```json
{
  "symbol": "XAUUSDT_UMCBL",
  "granularity": "5m",
  "start_time": "YYYY-MM-DDTHH:MM:SSZ",
  "end_time": "YYYY-MM-DDTHH:MM:SSZ",
  "expected_count": 0,
  "actual_count": 0,
  "missing_count": 0,
  "duplicate_count": 0,
  "gaps": [],
  "duplicates": [],
  "status": "passed"
}
```

**Result:** PASSED / WARNING / FAILED

### 15m Candles

```bash
uv run python tools/validate_candles.py \
  --symbol XAUUSDT_UMCBL \
  --granularity 15m \
  --start-time YYYY-MM-DDTHH:MM:SSZ \
  --end-time YYYY-MM-DDTHH:MM:SSZ \
  --db-url "$DATABASE_URL" \
  --output json
```

**Output:**
```json
{
  "symbol": "XAUUSDT_UMCBL",
  "granularity": "15m",
  "start_time": "YYYY-MM-DDTHH:MM:SSZ",
  "end_time": "YYYY-MM-DDTHH:MM:SSZ",
  "expected_count": 0,
  "actual_count": 0,
  "missing_count": 0,
  "duplicate_count": 0,
  "gaps": [],
  "duplicates": [],
  "status": "passed"
}
```

**Result:** PASSED / WARNING / FAILED

---

## REST vs DB Comparison

### 5m Comparison

```bash
uv run python tools/compare_candles.py \
  --symbol XAUUSDT_UMCBL \
  --granularity 5m \
  --start-time YYYY-MM-DDTHH:MM:SSZ \
  --end-time YYYY-MM-DDTHH:MM:SSZ \
  --db-url "$DATABASE_URL" \
  --tolerance 0.00000001 \
  --output json
```

**Output:**
```json
{
  "symbol": "XAUUSDT_UMCBL",
  "granularity": "5m",
  "start_time": "YYYY-MM-DDTHH:MM:SSZ",
  "end_time": "YYYY-MM-DDTHH:MM:SSZ",
  "tolerance": 0.00000001,
  "rest_count": 0,
  "db_count": 0,
  "matched": 0,
  "missing_in_db": 0,
  "extra_in_db": 0,
  "mismatched": 0,
  "mismatches": [],
  "status": "passed"
}
```

**Result:** PASSED / FAILED

### 15m Comparison

```bash
uv run python tools/compare_candles.py \
  --symbol XAUUSDT_UMCBL \
  --granularity 15m \
  --start-time YYYY-MM-DDTHH:MM:SSZ \
  --end-time YYYY-MM-DDTHH:MM:SSZ \
  --db-url "$DATABASE_URL" \
  --tolerance 0.00000001 \
  --output json
```

**Output:**
```json
{
  "symbol": "XAUUSDT_UMCBL",
  "granularity": "15m",
  "start_time": "YYYY-MM-DDTHH:MM:SSZ",
  "end_time": "YYYY-MM-DDTHH:MM:SSZ",
  "tolerance": 0.00000001,
  "rest_count": 0,
  "db_count": 0,
  "matched": 0,
  "missing_in_db": 0,
  "extra_in_db": 0,
  "mismatched": 0,
  "mismatches": [],
  "status": "passed"
}
```

**Result:** PASSED / FAILED

---

## Collector Log Summary

| Event | Count |
|---|---|
| WebSocket connections | 0 |
| Reconnects | 0 |
| Candle snapshots received | 0 |
| Candles persisted | 0 |
| Errors | 0 |

**Log file location:** `/var/log/xauusdt/collector.log`

---

## Issues Found

| # | Severity | Description | Action |
|---|---|---|---|
| 1 | — | — | — |

---

## Known Limitations

1. **Single WebSocket connection** — No connection pooling or failover
2. **No alerting** — Gaps detected only via manual `validate_candles.py`
3. **Public data only** — No private authenticated channels
4. **No message ordering across reconnects** — Handled via deduplication

---

## Recommendations

- [ ] Run `validate_candles.py` daily via cron for automated gap detection
- [ ] Set up PostgreSQL connection monitoring
- [ ] Consider adding WebSocket health check endpoint
- [ ] Evaluate multi-connection WebSocket for redundancy
- [ ] Add Telegram/email alerting for collector downtime

---

## Sign-off

| Field | Value |
|---|---|
| Smoke run status | PASSED / WARNING / FAILED |
| Data quality | Acceptable / Needs investigation |
| Next step | Proceed to backtest engine / Fix issues first |
| Signed by | — |
| Date | YYYY-MM-DD |
