"""Debug backfill - test OKX client + upsert."""

import sys
import asyncio
from datetime import UTC, datetime

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")

from xauusdt.storage.database import init_db, get_session
from xauusdt.exchange.okx_client import OKXClient
from xauusdt.storage.candle_repository import CandleRepository


async def main():
    await init_db("postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt")

    # 1. Fetch from OKX
    async with OKXClient() as client:
        candles = await client.fetch_candles(
            symbol="XAU-USDT-SWAP",
            granularity="15m",
            start_time=datetime(2026, 7, 14, tzinfo=UTC),
            limit=5,
        )
        print(f"Candles from OKX: {len(candles)}")
        if candles:
            print(f"  First: {candles[0].open_time} -> O={candles[0].open} H={candles[0].high} L={candles[0].low} C={candles[0].close}")

    # 2. Upsert to DB
    async for session in get_session():
        repo = CandleRepository(session)
        
        count_before = await repo.count_in_range("XAU-USDT-SWAP", "15m", 
            datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 16, tzinfo=UTC))
        print(f"\nBefore upsert: {count_before} candles")
        
        if candles:
            stored = await repo.upsert_many(candles)
            print(f"Upserted: {stored} candles")
        
        count_after = await repo.count_in_range("XAU-USDT-SWAP", "15m",
            datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 16, tzinfo=UTC))
        print(f"After upsert: {count_after} candles")
        
        # Query range
        if count_after > 0:
            results = await repo.query_by_range("XAU-USDT-SWAP", "15m",
                datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 16, tzinfo=UTC))
            print(f"\nQueried {len(results)} candles:")
            for r in results[:3]:
                print(f"  ts={r.timestamp} open={r.open_price} high={r.high} low={r.low} close={r.close}")
        
        session.close()
        break

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
