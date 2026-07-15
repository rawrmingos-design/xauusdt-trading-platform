"""Run full OKX backfill to PostgreSQL."""

import asyncio
import sys
from datetime import UTC, datetime

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")

from xauusdt.collectors.okx_backfill import _download_okx_all
from xauusdt.exchange.okx_client import OKXClient
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db


async def main():
    await init_db("postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt")

    start = datetime(2026, 7, 8, tzinfo=UTC)
    end = datetime(2026, 7, 15, tzinfo=UTC)

    print(f"OKX Backfill: {start.date()} to {end.date()} (15m)")
    print("This may take 2-3 minutes...\n")

    async with OKXClient() as client:
        async for session in get_session():
            repo = CandleRepository(session)
            result = await _download_okx_all(client, repo, "15m", start, end, dry_run=False)
            print(f"Downloaded: {result.downloaded_count}")
            print(f"Stored: {result.stored_count}")
            print(f"Gaps: {result.gap_count}")
            print(f"Status: {result.status}")
            session.close()
            break

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
