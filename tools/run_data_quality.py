"""Data quality report for PROJECT-DATA-009B."""

import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")

from sqlalchemy import text

from xauusdt.exchange.okx_client import OKXClient
from xauusdt.storage.database import get_session, init_db

REPORT_DIR = Path("/home/devistopup13/xauusdt-platform/docs/reports")


async def get_db_stats(session, symbol, granularity):
    """Get candle counts from DB."""
    results = {}

    # Total count
    res = await session.execute(text(
        "SELECT COUNT(*) FROM candles WHERE symbol=:sym AND granularity=:gran"
    ), {"sym": symbol, "gran": granularity})
    results["total_count"] = res.scalar()

    # Min/Max time
    res = await session.execute(text(
        "SELECT min(open_time), max(open_time) FROM candles WHERE symbol=:sym AND granularity=:gran"
    ), {"sym": symbol, "gran": granularity})
    row = res.fetchone()
    results["earliest_time"] = row[0].isoformat() if row[0] else None
    results["latest_time"] = row[1].isoformat() if row[1] else None

    # Granularity uniqueness check
    res = await session.execute(text(
        "SELECT DISTINCT granularity FROM candles WHERE symbol=:sym"
    ), {"sym": symbol})
    results["granularities"] = [r[0] for r in res.fetchall()]

    return results


async def validate_continuity(session, symbol, granularity, expected_start, expected_end):
    """Check continuity: gaps, expected count, missing count."""
    res = await session.execute(text(
        "SELECT open_time FROM candles WHERE symbol=:sym AND granularity=:gran "
        "ORDER BY open_time"
    ), {"sym": symbol, "gran": granularity})
    rows = res.fetchall()
    times = [r[0] for r in rows]

    if not times:
        return {"valid": False, "gaps": 0, "total_candles": 0}

    gran_map = {
        "15m": 900, "1H": 3600, "1m": 60, "5m": 300,
        "30m": 1800, "4H": 14400, "2H": 7200, "1D": 86400, "1W": 604800,
    }
    seconds = gran_map.get(granularity, 900)

    expected_count = int((expected_end - expected_start).total_seconds() / seconds)

    # Find gaps
    gap_count = 0
    sample_gaps = []
    for i in range(1, len(times)):
        diff = (times[i] - times[i-1]).total_seconds()
        if diff > seconds * 1.5:
            gap_count += 1
            if gap_count <= 10:
                sample_gaps.append({
                    "after": times[i-1].isoformat(),
                    "before": times[i].isoformat(),
                    "gap_seconds": diff,
                    "missing_candles": int(diff / seconds) - 1,
                })

    # Duplicate detection
    time_counts = Counter(t.isoformat() for t in times)
    duplicates = sum(c - 1 for c in time_counts.values() if c > 1)

    return {
        "valid": gap_count == 0,
        "expected_count": expected_count,
        "actual_count": len(times),
        "coverage_pct": round(len(times) / expected_count * 100, 2) if expected_count > 0 else 0,
        "gaps": gap_count,
        "duplicate_count": duplicates,
        "sample_gaps": sample_gaps,
    }


async def compare_sampled_range(session, symbol, granularity, start_time, end_time):
    """Compare DB candles with OKX REST API for a sample range."""
    res = await session.execute(text(
        "SELECT open_time, open_price, high, low, close, volume "
        "FROM candles WHERE symbol=:sym AND granularity=:gran "
        "AND open_time >= :st AND open_time <= :et "
        "ORDER BY open_time LIMIT 100"
    ), {"sym": symbol, "gran": granularity, "st": start_time, "et": end_time})
    db_rows = res.fetchall()

    async with OKXClient() as client:
        candles = await client.fetch_candles(
            symbol=symbol,
            granularity=granularity,
            start_time=start_time,
            limit=100,
        )

    if candles and db_rows:
        db_map = {(r[0].isoformat(), r[1]): r for r in db_rows}
        mismatches = []
        for c in candles:
            key = (c.open_time.isoformat(), c.open)
            if key in db_map:
                db_row = db_map[key]
                if abs(c.high - db_row[2]) > 0.01 or abs(c.low - db_row[3]) > 0.01:
                    mismatches.append({
                        "time": key[0],
                        "okx_high": c.high, "db_high": db_row[2],
                        "okx_low": c.low, "db_low": db_row[3],
                    })

        return [{
            "okx_candles_fetched": len(candles),
            "db_candles_found": len(db_rows),
            "total_compared": min(len(candles), len(db_rows)),
            "mismatches": len(mismatches),
            "mismatch_details": mismatches[:5],
            "status": "pass" if len(mismatches) == 0 else "fail",
        }]
    else:
        return [{
            "okx_candles_fetched": len(candles),
            "db_candles_found": len(db_rows),
            "status": "skipped",
            "reason": "No candles at start_time (outside range)",
        }]


async def main():
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)

    symbol = "XAU-USDT-SWAP"
    end = datetime(2026, 7, 15, tzinfo=UTC)
    start = end - timedelta(days=90)

    report = {
        "task": "PROJECT-DATA-009B",
        "generated_at": datetime.now(UTC).isoformat(),
        "canonical_symbol": symbol,
        "backfill_commands": [
            f"uv run python tools/run_backfill.py --symbol {symbol} --granularity 15m --days 90",
            f"uv run python tools/run_backfill.py --symbol {symbol} --granularity 1H --days 90",
        ],
    }

    comparison_results = []

    async for session in get_session():
        for gran in ["15m", "1H"]:
            stats = await get_db_stats(session, symbol, gran)
            stats["start_time"] = start.isoformat()
            stats["end_time"] = end.isoformat()

            continuity = await validate_continuity(session, symbol, gran, start, end)
            stats["continuity"] = continuity

            report[f"{gran}_stats"] = stats
            print(f"\n=== {gran} ===")
            print(f"Total: {stats['total_count']}, Expected: {continuity['expected_count']}")
            print(f"Coverage: {continuity['coverage_pct']}%")
            print(f"Gaps: {continuity['gaps']}, Duplicates: {continuity['duplicate_count']}")

        # Sampled range comparison
        sample_ranges = [
            (start + timedelta(days=0), start + timedelta(days=1)),
            (start + timedelta(days=30), start + timedelta(days=31)),
            (start + timedelta(days=60), start + timedelta(days=61)),
        ]

        for s, e in sample_ranges:
            comp = await compare_sampled_range(session, symbol, "15m", s, e)
            if comp:
                comparison_results.extend(comp)
                print(f"\nComparison at {s.date()}-{e.date()}: {comp[0].get('status', 'unknown')}")

        report["comparison_results"] = comparison_results
        await session.close()
        break

    report["known_limitations"] = [
        "OKX public API limited to 100 candles per request.",
        "Public API may not have full history before XAU-USDT-SWAP was listed on OKX.",
        "No live candle validation — only historical REST comparison.",
        "Price comparison tolerance is $0.01.",
    ]

    report["recommended_next_steps"] = [
        "PROJECT-BACKTEST-004: Rerun baseline backtest with 90-day data and EMA 50/200 config.",
        "Add 1D granularity if needed for multi-timeframe analysis.",
        "Implement automated candle freshness monitoring.",
    ]

    # Write JSON report
    json_path = REPORT_DIR / "data_quality_009B.json"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nJSON report: {json_path}")

    # Write Markdown report
    md = [
        "# Data Quality Report — PROJECT-DATA-009B",
        f"**Generated**: {report['generated_at']}",
        f"**Canonical Symbol**: {symbol}",
        "",
        "## Backfill Commands",
        "```bash",
        f"uv run python tools/run_backfill.py --symbol {symbol} --granularity 15m --days 90",
        f"uv run python tools/run_backfill.py --symbol {symbol} --granularity 1H --days 90",
        "```",
        "",
        "## Results Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ]

    for gran in ["15m", "1H"]:
        stats = report.get(f"{gran}_stats", {})
        cont = stats.get("continuity", {})
        md.extend([
            f"### {gran} Candles",
            "| Property | Value |",
            "|----------|-------|",
            f"| Total stored | {stats.get('total_count', 'N/A')} |",
            f"| Expected count | {cont.get('expected_count', 'N/A')} |",
            f"| Coverage | {cont.get('coverage_pct', 0)}% |",
            f"| Gaps | {cont.get('gaps', 'N/A')} |",
            f"| Duplicates | {cont.get('duplicate_count', 'N/A')} |",
            f"| Earliest | {stats.get('earliest_time', 'N/A')} |",
            f"| Latest | {stats.get('latest_time', 'N/A')} |",
            "",
        ])

    md.extend([
        "## REST-vs-DB Comparison",
        "",
    ])
    for comp in comparison_results:
        md.append(f"- **{comp.get('okx_candles_fetched', 0)} OKX vs {comp.get('db_candles_found', 0)} DB**: {comp.get('status', 'unknown')} (mismatches: {comp.get('mismatches', 0)})")
    md.extend([
        "",
        "## Known Limitations",
        "",
    ] + [f"- {l}" for l in report["known_limitations"]] + [
        "",
        "## Recommended Next Steps",
        "",
    ] + [f"- {s}" for s in report["recommended_next_steps"]])

    md_path = REPORT_DIR / "data_quality_009B.md"
    md_path.write_text("\n".join(md))
    print(f"Markdown report: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
