#!/usr/bin/env python3
"""Compare stored candles against Bitget REST historical candles.

Read-only tool. Never modifies database contents.

Usage:
    uv run python tools/compare_candles.py \\
        --symbol XAU-USDT-SWAP \\
        --granularity 15m \\
        --start-time 2026-07-14T00:00:00Z \\
        --end-time 2026-07-14T12:00:00Z \\
        --db-url "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt" \\
        --tolerance 0.00000001 \\
        --output json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from xauusdt.exchange.client import BitgetPublicClient
from xauusdt.exchange.models import Candle as ExchangeCandle
from xauusdt.storage.models import CandleOrm

NUMERIC_FIELDS = {"open", "high", "low", "close", "volume", "quote_volume"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare stored candles against REST historical candles."
    )
    parser.add_argument("--symbol", required=True, help="e.g. XAU-USDT-SWAP")
    parser.add_argument("--granularity", required=True, help="1m, 5m, 15m, 30m, 1H, 4H, 1D")
    parser.add_argument("--start-time", required=True, help="ISO 8601 UTC start")
    parser.add_argument("--end-time", required=True, help="ISO 8601 UTC end")
    parser.add_argument("--db-url", required=True, help="SQLAlchemy database URL")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.00000001,
        help="Numeric tolerance for float comparison (default: 1e-8)",
    )
    parser.add_argument(
        "--output",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text)",
    )
    return parser.parse_args(argv)


def parse_iso8601(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def fields_approx_match(
    db_fields: dict[str, Any],
    rest_fields: dict[str, Any],
    tolerance: float,
) -> tuple[bool, list[str]]:
    """Compare numeric fields with configurable tolerance.

    Returns (matched, list_of_mismatched_field_names).
    """
    mismatches: list[str] = []
    for field in NUMERIC_FIELDS:
        db_val = db_fields.get(field)
        rest_val = rest_fields.get(field)
        if db_val is None and rest_val is None:
            continue
        if db_val is None or rest_val is None:
            mismatches.append(f"{field}: DB={db_val!r} REST={rest_val!r}")
            continue
        try:
            db_f = float(db_val)
            rest_f = float(rest_val)
            if abs(db_f - rest_f) > tolerance:
                mismatches.append(
                    f"{field}: DB={db_f} REST={rest_f} "
                    f"(diff={abs(db_f - rest_f):.10g} > {tolerance})"
                )
        except (TypeError, ValueError):
            mismatches.append(f"{field}: DB={db_val!r} REST={rest_val!r}")
    return len(mismatches) == 0, mismatches


async def fetch_rest_candles(
    client: BitgetPublicClient,
    symbol: str,
    granularity: str,
    start_time: datetime,
    end_time: datetime,
) -> dict[datetime, ExchangeCandle]:
    """Fetch all candles from REST API in paginated chunks."""
    candles: dict[datetime, ExchangeCandle] = {}
    limit = 200
    cursor = start_time

    while cursor < end_time:
        batch = await client.get_history_candles(
            symbol=symbol,
            granularity=granularity,
            limit=limit,
            start_time=cursor,
        )
        if not batch:
            break

        for c in batch:
            if c.open_time < end_time:
                candles[c.open_time] = c

        # Advance cursor: use the last candle's open_time + 1ms to avoid overlap
        last_open = max(c.open_time for c in batch)
        cursor = last_open

        if len(batch) < limit:
            break

    return candles


async def fetch_db_candles(
    db_url: str,
    symbol: str,
    granularity: str,
    start_time: datetime,
    end_time: datetime,
) -> dict[datetime, dict[str, Any]]:
    """Fetch all candles from database."""
    engine = create_async_engine(db_url, echo=False)
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
            rows = result.scalars().all()
    finally:
        await engine.dispose()

    db_map: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        db_map[row.open_time] = {
            "open": row.open_price,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
            "quote_volume": row.quote_volume,
        }
    return db_map


async def compare(args: argparse.Namespace) -> dict[str, Any]:
    """Run comparison and return report dict."""
    symbol = args.symbol
    granularity = args.granularity
    start_time = parse_iso8601(args.start_time)
    end_time = parse_iso8601(args.end_time)
    tolerance = args.tolerance

    # Fetch REST candles
    client = BitgetPublicClient()

    try:
        rest_candles = await fetch_rest_candles(client, symbol, granularity, start_time, end_time)
    except Exception as e:
        return {
            "symbol": symbol,
            "granularity": granularity,
            "status": "failed",
            "error": f"REST API error: {e}",
            "rest_count": 0,
            "db_count": 0,
            "matched": 0,
            "missing_in_db": 0,
            "mismatched": 0,
        }

    # Fetch DB candles
    try:
        db_candles = await fetch_db_candles(args.db_url, symbol, granularity, start_time, end_time)
    except Exception as e:
        return {
            "symbol": symbol,
            "granularity": granularity,
            "status": "failed",
            "error": f"DB error: {e}",
            "rest_count": len(rest_candles),
            "db_count": 0,
            "matched": 0,
            "missing_in_db": 0,
            "mismatched": 0,
        }

    # Compare
    mismatches: list[dict[str, Any]] = []
    matched = 0
    missing_in_db = 0
    extra_in_db = 0

    for ts, rest_fields in rest_candles.items():
        if ts not in db_candles:
            missing_in_db += 1
        else:
            db_fields = db_candles[ts]
            ok, details = fields_approx_match(db_fields, rest_fields, tolerance)
            if ok:
                matched += 1
            else:
                mismatches.append(
                    {
                        "open_time": ts.isoformat(),
                        "details": details,
                    }
                )

    extra_in_db = len(db_candles) - matched - missing_in_db

    # Count mismatched candle count (unique timestamps with any mismatch)
    mismatched_count = len(mismatches)

    total_rest = len(rest_candles)
    total_db = len(db_candles)

    # Status
    if mismatched_count > 0 or missing_in_db > 0:
        status = "failed"
    elif extra_in_db > 0:
        status = "warning"
    else:
        status = "passed"

    return {
        "symbol": symbol,
        "granularity": granularity,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "tolerance": tolerance,
        "rest_count": total_rest,
        "db_count": total_db,
        "matched": matched,
        "missing_in_db": missing_in_db,
        "extra_in_db": extra_in_db,
        "mismatched": mismatched_count,
        "mismatches": mismatches[:20],  # Limit detailed output
        "status": status,
    }


def human_readable(report: dict[str, Any]) -> str:
    lines = [
        "=== REST vs DB Candle Comparison ===",
        "",
        f"Symbol:      {report['symbol']}",
        f"Granularity: {report['granularity']}",
    ]
    if "start_time" in report and "end_time" in report:
        lines.append(f"Period:      {report['start_time']} → {report['end_time']}")
    if "tolerance" in report:
        lines.append(f"Tolerance:   {report.get('tolerance', 'N/A')}")
    lines.append("")
    lines.append(f"REST candles: {report.get('rest_count', 'N/A')}")
    lines.append(f"DB candles:   {report.get('db_count', 'N/A')}")
    lines.append(f"Matched:      {report.get('matched', 'N/A')}")
    lines.append(f"Missing in DB: {report.get('missing_in_db', 'N/A')}")
    lines.append(f"Extra in DB:   {report.get('extra_in_db', 'N/A')}")
    lines.append(f"Mismatched:    {report.get('mismatched', 'N/A')}")
    lines.append("")
    lines.append(f"Status:      {report.get('status', 'unknown').upper()}")

    if report.get("error"):
        lines.insert(1, f"ERROR: {report['error']}")
    if report.get("mismatches"):
        lines.append("")
        lines.append("Mismatches (showing up to 20):")
        for m in report["mismatches"][:20]:
            lines.append(f"  {m['open_time']}: {', '.join(m['details'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        report = asyncio.run(compare(args))
    except Exception as e:
        report = {
            "symbol": args.symbol,
            "granularity": args.granularity,
            "status": "failed",
            "error": str(e),
            "rest_count": 0,
            "db_count": 0,
            "matched": 0,
            "missing_in_db": 0,
            "mismatched": 0,
        }

    if args.output == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        if "error" in report and report["status"] == "failed":
            print(f"ERROR: {report['error']}", file=sys.stderr)
            return 2
        print(human_readable(report))

    status = report.get("status", "failed")
    if status == "passed":
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
