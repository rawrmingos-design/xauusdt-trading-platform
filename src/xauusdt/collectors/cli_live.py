"""CLI entry point for live WebSocket candle collection."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from xauusdt.collectors.live_candles import LIVE_GRANULARITIES, LiveCandleCollector
from xauusdt.exchange.websocket import BitgetWebSocketClient
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import create_tables, init_db

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL = "XAUUSDT_UMCBL"
DB_URL = "sqlite+aiosqlite:///xauusdt.db"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xauusdt-collect",
        description="Live WebSocket candle collector for Bitget XAUUSDT futures",
    )
    parser.add_argument(
        "--symbol",
        default=DEFAULT_SYMBOL,
        help="Futures symbol (default: XAUUSDT_UMCBL)",
    )
    parser.add_argument(
        "--granularities",
        default=",".join(sorted(LIVE_GRANULARITIES)),
        help=f"Comma-separated granularities to collect (default: {','.join(sorted(LIVE_GRANULARITIES))})",
    )
    parser.add_argument(
        "--db-url",
        default=DB_URL,
        help=f"Database URL (default: {DB_URL})",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser


async def _run_collector(args: argparse.Namespace) -> None:
    """Execute live candle collection."""
    await init_db(args.db_url)
    await create_tables()

    granularities = {g.strip() for g in args.granularities.split(",") if g.strip()}
    for g in granularities:
        if g not in LIVE_GRANULARITIES:
            print(
                f"ERROR: Unsupported granularity {g!r}. Allowed: {sorted(LIVE_GRANULARITIES)}",
                file=sys.stderr,
            )
            sys.exit(1)

    client = BitgetWebSocketClient()
    engine = create_async_engine(args.db_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    repo = CandleRepository(session)

    collector = LiveCandleCollector(
        client=client,
        repository=repo,
        symbol=args.symbol,
        granularities=granularities,
    )

    # Setup signal handler for graceful shutdown
    loop = asyncio.get_event_loop()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        asyncio.get_event_loop().stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        logger.info(
            "Starting live collector for %s with granularities %s ...",
            args.symbol,
            sorted(granularities),
        )
        await collector.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        await collector.stop()
        await engine.dispose()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    asyncio.run(_run_collector(args))


if __name__ == "__main__":
    main()
