# PROJECT-DATA-008-OKX: Live Collector Smoke Test Report

## Summary
- **Status**: PASS
- **Quality Score**: 100.00%
- **Started**: 2026-07-15T07:11:42.724990+00:00
- **Ended**: 2026-07-15T07:12:42.820142+00:00
- **Duration**: 60.1s (1.0m)

## Configuration
| Setting | Value |
|---|---|
| Exchange | okx |
| Symbol | XAU-USDT-SWAP |
| Granularity | 5m |
| DB URL | `sqlite+aiosqlite:///smoke_test.db` |

## Collection Summary
| Metric | Value |
|---|---|
| Stored Candles | 5 |
| Expected Candles | 0 |
| Coverage Ratio | 0.00% |

## Validation Results

### Continuity
| Metric | Value |
|---|---|
| Expected | 5 |
| Stored | 5 |
| Gaps Found | 0 |
| Continuity Ratio | 100.0000% |
| Status | PASS |

### REST vs DB Comparison
| Metric | Value |
|---|---|
| Matched | 5 |
| Mismatched | 0 |
| REST Only | 95 |
| DB Only | 0 |
| Quality Score | 100.0000% |
| Status | PASS |

## Errors
None

## Known Limitations
- Limited by container lifetime
- SQLite used instead of PostgreSQL

## Commands Used
```bash
python tools/smoke_test_okx_collector.py
  --granularity 5m
  --duration 60
```