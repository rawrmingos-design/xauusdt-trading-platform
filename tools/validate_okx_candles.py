#!/usr/bin/env python3
"""Validate XAUUSDT candle data quality.

Compares candle data from the database against OKX public API
to verify correctness.

Usage:
    PYTHONPATH= uv run python tools/validate_okx_candles.py
    PYTHONPATH= uv run python tools/validate_okx_candles.py --no-okx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

from xauusdt.collectors.cli import _parse_datetime
from xauusdt.exchange.okx_client import OKXClient
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import init_db

DB_URL = "sqlite+aiosqlite:///xauusdt.db"


async def validate(
    granularity: str,
    start_time: datetime,
    end_time: datetime,
    db_url: str,
    okx_api: bool,
) -> dict:
    """Validate candle data against database and optionally OKX API."""
    results: dict[str, Any] = {
        "granularity": granularity,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "db_count": 0,
        "okx_count": 0,
        "okx_match_count": 0,
        "okx_mismatch_count": 0,
        "errors": [],
    }

    # Fetch from database
    await init_db(db_url)
    db_candles: list[Any] = []
    async for session in _get_session(db_url):
        repo = CandleRepository(session)
        db_candles = await repo.query_by_range(
            symbol="XAUUSDT_UMCBL",
            granularity=granularity,
            start_time=start_time,
            end_time=end_time,
        )
        results["db_count"] = len(db_candles)
        break

    # Fetch from OKX API (if enabled)
    if okx_api:
        async with OKXClient() as client:
            okx_candles = await client.fetch_candles(
                symbol="XAUUSDT_UMCBL",
                granularity=granularity,
                start_time=start_time,
                limit=100,
            )
            results["okx_count"] = len(okx_candles)

            # Compare with DB candles — use open_time as key
            db_by_time: dict[str, Any] = {}
            for c in db_candles:
                db_by_time[c.open_time.isoformat()] = c

            for oc in okx_candles:
                key = oc.open_time.isoformat()
                if key in db_by_time:
                    dc = db_by_time[key]
                    dc_open = float(getattr(dc, "open_price", 0))
                    tolerance = max(dc_open * 0.0001, 0.01)
                    if abs(float(oc.open) - dc_open) > tolerance:
                        results["okx_mismatch_count"] += 1
                        results["errors"].append(
                            f"Open mismatch at {key}: db={dc_open} okx={float(oc.open)}"
                        )
                    elif abs(float(oc.high) - float(getattr(dc, "high", 0))) > tolerance:
                        results["okx_mismatch_count"] += 1
                    elif abs(float(oc.low) - float(getattr(dc, "low", 0))) > tolerance:
                        results["okx_mismatch_count"] += 1
                    elif abs(float(oc.close) - float(getattr(dc, "close", 0))) > tolerance:
                        results["okx_mismatch_count"] += 1
                    else:
                        results["okx_match_count"] += 1
                else:
                    results["okx_mismatch_count"] += 1
                    results["errors"].append(f"No DB record for {key}")

    results["status"] = "passed" if results["okx_mismatch_count"] == 0 else "failed"
    return results


async def _get_session(database_url: str):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate XAUUSDT candle data")
    parser.add_argument(
        "--granularity",
        default="15m",
        choices=["5m", "15m", "1H", "4H"],
        help="Candle granularity",
    )
    parser.add_argument(
        "--start-time",
        type=_parse_datetime,
        default=datetime(2026, 7, 14, 0, 0, 0, tzinfo=UTC),
        help="Start time",
    )
    parser.add_argument(
        "--end-time",
        type=_parse_datetime,
        default=datetime(2026, 7, 15, 0, 0, 0, tzinfo=UTC),
        help="End time",
    )
    parser.add_argument("--db-url", default=DB_URL, help="Database URL")
    parser.add_argument("--no-okx", action="store_true", help="Skip OKX API comparison")
    args = parser.parse_args()

    async def _run() -> None:
        try:
            result = await validate(
                granularity=args.granularity,
                start_time=args.start_time,
                end_time=args.end_time,
                db_url=args.db_url,
                okx_api=not args.no_okx,
            )
            print(json.dumps(result, indent=2))
            sys.exit(0 if result["status"] == "passed" else 1)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            sys.exit(2)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
