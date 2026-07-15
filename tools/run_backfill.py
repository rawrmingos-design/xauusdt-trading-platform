"""Run OKX backfill with configurable symbol, granularity, and date range."""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")

from xauusdt.collectors.okx_backfill import _download_okx_all
from xauusdt.exchange.okx_client import OKXClient
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db


async def run_backfill(
    db_url: str, symbol: str, granularity: str, days: int, dry_run: bool = False
):
    """Backfill OKX candles for given symbol, granularity, and range."""
    try:
        await init_db(db_url)

        end = datetime(2026, 7, 15, tzinfo=UTC)
        start = end - timedelta(days=days)

        print(f"OKX Backfill: {symbol} | {granularity}")
        print(f"Range: {start.date()} to {end.date()} ({days} days)")
        print(f"Dry run: {dry_run}\n")

        async with OKXClient() as client:
            async for session in get_session():
                repo = CandleRepository(session)
                print("Starting download...")
                try:
                    result = await _download_okx_all(
                        client, repo, granularity, start, end, dry_run=dry_run
                    )
                    print(f"\nDownloaded: {result.downloaded_count}")
                    print(f"Stored: {result.stored_count}")
                    print(f"Gaps: {result.gap_count}")
                    print(f"Status: {result.status}")
                except Exception:
                    import traceback

                    traceback.print_exc()
                finally:
                    await session.close()
                break

        print("\nDone!")
    except Exception as e:
        print(f"Top level error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Backfill OKX XAU-USDT-SWAP candles to PostgreSQL")
    parser.add_argument(
        "--db-url",
        default="postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt",
        help="PostgreSQL connection URL",
    )
    parser.add_argument("--symbol", default="XAU-USDT-SWAP", help="OKX instrument symbol")
    parser.add_argument(
        "--granularity", default="15m", help="Candle granularity (1m, 5m, 15m, 30m, 1H, 4H, 1D)"
    )
    parser.add_argument("--days", type=int, default=90, help="Number of days to backfill")
    parser.add_argument(
        "--dry-run", action="store_true", help="Calculate expected count without saving"
    )
    args = parser.parse_args()

    asyncio.run(
        run_backfill(args.db_url, args.symbol, args.granularity, args.days, dry_run=args.dry_run)
    )


if __name__ == "__main__":
    main()
