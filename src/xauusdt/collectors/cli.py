"""CLI entry point for historical backfill (Bitget or OKX)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from xauusdt.collectors import (
    BACKFILL_GRANULARITIES,
    BackfillResult,
    HistoricalBackfillService,
)
from xauusdt.collectors.okx_backfill import OKXBackfillResult, _download_okx_all
from xauusdt.exchange.client import BitgetPublicClient
from xauusdt.exchange.okx_client import OKXClient
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import close_db, create_tables, init_db

DEFAULT_SYMBOL = "XAUUSDT_UMCBL"
DB_URL = "sqlite+aiosqlite:///xauusdt.db"


def _parse_datetime(value: str) -> datetime:
    """Parse ISO-8601 datetime string."""
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid datetime {value!r}. Use ISO-8601 format "
            f"(e.g. 2025-01-01T00:00:00Z or 2025-01-01T00:00:00+00:00)"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xauusdt-backfill",
        description="Historical backfill for XAUUSDT candles (Bitget or OKX)",
    )
    parser.add_argument(
        "--exchange",
        choices=["bitget", "okx"],
        default="bitget",
        help="Exchange to fetch data from (default: bitget)",
    )
    parser.add_argument(
        "--symbol",
        default=DEFAULT_SYMBOL,
        help="Futures symbol (default: XAUUSDT_UMCBL)",
    )
    parser.add_argument(
        "--granularity",
        required=True,
        choices=sorted(BACKFILL_GRANULARITIES),
        help="Candle granularity (5m, 15m, 1H, 4H)",
    )
    parser.add_argument(
        "--start-time",
        type=_parse_datetime,
        required=True,
        help="Inclusive start time (ISO-8601)",
    )
    parser.add_argument(
        "--end-time",
        type=_parse_datetime,
        required=True,
        help="Exclusive end time (ISO-8601)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and validate without writing to storage",
    )
    parser.add_argument(
        "--db-url",
        default=DB_URL,
        help=f"Database URL (default: {DB_URL})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write result JSON to file instead of stdout",
    )
    return parser


async def _run_backfill(args: argparse.Namespace) -> None:
    """Execute backfill with initialized DB and client."""
    await init_db(args.db_url)
    await create_tables()

    result: BackfillResult | OKXBackfillResult | None = None

    if args.exchange == "bitget":
        async with BitgetPublicClient() as client:
            async for session in _get_session(args.db_url):
                repo = CandleRepository(session)
                service = HistoricalBackfillService(client, repo)
                result = await service.run(
                    granularity=args.granularity,
                    start_time=args.start_time,
                    end_time=args.end_time,
                    dry_run=args.dry_run,
                )
                break

    elif args.exchange == "okx":
        async with OKXClient() as client:
            async for session in _get_session(args.db_url):
                repo = CandleRepository(session)
                result = await _download_okx_all(
                    client=client,
                    repository=repo,
                    granularity=args.granularity,
                    start_time=args.start_time,
                    end_time=args.end_time,
                    dry_run=args.dry_run,
                )
                break

    await close_db()

    if result is None:
        print("ERROR: no database session available", file=sys.stderr)
        sys.exit(1)

    output_json = json.dumps(result.to_dict(), indent=2)
    if args.output:
        args.output.write_text(output_json)
        print(f"Result written to {args.output}")
    else:
        print(output_json)


async def _get_session(database_url: str):  # type: ignore[no-untyped-def]
    """Yield a single database session."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run_backfill(args))


if __name__ == "__main__":
    main()
