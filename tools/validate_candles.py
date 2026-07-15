#!/usr/bin/env python3
"""Validate stored candle continuity, gaps, and duplicates.

Read-only tool. Never modifies database contents.

Usage:
    uv run python tools/validate_candles.py \\
        --symbol XAU-USDT-SWAP \\
        --granularity 15m \\
        --start-time 2026-07-14T00:00:00Z \\
        --end-time 2026-07-14T12:00:00Z \\
        --db-url "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt" \\
        --output json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from xauusdt.storage.models import CandleOrm

GRANULARITY_DELTA: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1H": timedelta(hours=1),
    "4H": timedelta(hours=4),
    "1D": timedelta(days=1),
}

GRANULARITY_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate stored candle continuity and gaps.")
    parser.add_argument("--symbol", required=True, help="e.g. XAU-USDT-SWAP")
    parser.add_argument("--granularity", required=True, help="1m, 5m, 15m, 30m, 1H, 4H, 1D")
    parser.add_argument(
        "--start-time",
        required=True,
        help="ISO 8601 UTC start, e.g. 2026-07-14T00:00:00Z",
    )
    parser.add_argument(
        "--end-time",
        required=True,
        help="ISO 8601 UTC end, e.g. 2026-07-14T12:00:00Z",
    )
    parser.add_argument(
        "--db-url",
        required=True,
        help="SQLAlchemy database URL",
    )
    parser.add_argument(
        "--output",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text)",
    )
    return parser.parse_args(argv)


def parse_iso8601(s: str) -> datetime:
    """Parse ISO 8601 UTC timestamp."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def human_readable(report: dict[str, Any]) -> str:
    """Format validation report as human-readable summary."""
    lines = [
        "=== Candle Validation Report ===",
        "",
        f"Symbol:      {report['symbol']}",
        f"Granularity: {report['granularity']}",
        f"Period:      {report['start_time']} → {report['end_time']}",
        "",
        f"Expected:    {report['expected_count']} candles",
        f"Actual:      {report['actual_count']} candles",
        f"Missing:     {report['missing_count']} candles",
        f"Duplicate:   {report['duplicate_count']} candles",
        "",
        f"Status:      {report['status'].upper()}",
    ]
    if report["missing_count"] > 0:
        lines.append("")
        lines.append("Missing intervals:")
        for gap_start, gap_end in report["gaps"]:
            lines.append(f"  {gap_start} → {gap_end}")
    if report["duplicates"]:
        lines.append("")
        lines.append("Duplicate keys:")
        for dup in report["duplicates"]:
            lines.append(f"  {dup}")
    return "\n".join(lines)


async def validate(args: argparse.Namespace) -> dict[str, Any]:
    """Run validation and return report dict."""
    symbol = args.symbol
    granularity = args.granularity
    start_time = parse_iso8601(args.start_time)
    end_time = parse_iso8601(args.end_time)
    delta = GRANULARITY_DELTA.get(granularity)
    gran_secs = GRANULARITY_SECONDS.get(granularity)

    if delta is None:
        return {
            "symbol": symbol,
            "granularity": granularity,
            "status": "failed",
            "error": f"Unsupported granularity {granularity!r}",
        }
    if gran_secs is None:
        return {
            "symbol": symbol,
            "granularity": granularity,
            "status": "failed",
            "error": f"Unknown granularity seconds for {granularity!r}",
        }
    if start_time >= end_time:
        return {
            "symbol": symbol,
            "granularity": granularity,
            "status": "failed",
            "error": "start-time must be before end-time",
        }

    expected_count = int((end_time - start_time).total_seconds() / gran_secs)

    engine = create_async_engine(args.db_url, echo=False)
    try:
        async with AsyncSession(engine) as session:
            stmt = (
                select(CandleOrm)
                .where(
                    CandleOrm.symbol == symbol,
                    CandleOrm.granularity == granularity,
                    CandleOrm.open_time >= start_time,
                    CandleOrm.open_time < end_time,
                )
                .order_by(CandleOrm.open_time)
            )
            result = await session.execute(stmt)
            candles = result.scalars().all()
    finally:
        await engine.dispose()

    # Deduplication check
    seen: dict[datetime, int] = {}
    for c in candles:
        seen[c.open_time] = seen.get(c.open_time, 0) + 1
    duplicate_keys: list[str] = []
    duplicate_count = 0
    for dt, count in seen.items():
        if count > 1:
            duplicate_count += count - 1
            duplicate_keys.append(dt.isoformat())

    # De-duplicated candle list for continuity check
    unique_candles = sorted(set(c.open_time for c in candles))
    actual_count = len(unique_candles)

    # Gap detection: generate all expected intervals and find missing ones
    expected_times: list[datetime] = []
    t = start_time
    while t < end_time:
        expected_times.append(t)
        t += delta

    expected_set = set(expected_times)
    actual_set = set(unique_candles)
    missing_times = sorted(expected_set - actual_set)

    # Group consecutive missing into gap ranges
    gaps: list[tuple[str, str]] = []
    if missing_times:
        gap_start = missing_times[0]
        gap_end = missing_times[0]
        for i in range(1, len(missing_times)):
            if missing_times[i] - gap_end <= delta:
                gap_end = missing_times[i]
            else:
                gaps.append((gap_start.isoformat(), gap_end.isoformat()))
                gap_start = missing_times[i]
                gap_end = missing_times[i]
        gaps.append((gap_start.isoformat(), gap_end.isoformat()))

    # Status determination
    if duplicate_count > 0:
        status = "failed"
    elif len(missing_times) > 0:
        status = "warning"
    else:
        status = "passed"

    return {
        "symbol": symbol,
        "granularity": granularity,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "expected_count": expected_count,
        "actual_count": actual_count,
        "missing_count": len(missing_times),
        "duplicate_count": duplicate_count,
        "gaps": gaps,
        "duplicates": duplicate_keys,
        "status": status,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        report = asyncio.run(validate(args))
    except Exception as e:
        report = {
            "symbol": args.symbol,
            "granularity": args.granularity,
            "status": "failed",
            "error": str(e),
        }

    if args.output == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        if "error" in report:
            print(f"ERROR: {report['error']}", file=sys.stderr)
            return 2
        print(human_readable(report))

    status = report.get("status", "failed")
    if status == "passed":
        return 0
    elif status == "warning":
        # Warnings: gaps but no duplicates — still exit 1 for alerting
        return 1
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
