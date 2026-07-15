"""Check backfilled data."""

import asyncio

import asyncpg


async def check():
    conn = await asyncpg.connect(
        host="localhost", port=5432, user="xauusdt", password="xauusdt", database="xauusdt"
    )

    total = await conn.fetchval("SELECT COUNT(*) FROM candles")
    print(f"Total candles: {total}")

    grans = await conn.fetch("SELECT granularity, COUNT(*) FROM candles GROUP BY granularity")
    for g, c in grans:
        print(f"  {g}: {c}")

    range_15m = await conn.fetchrow(
        "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM candles WHERE granularity='15m'"
    )
    if range_15m and range_15m[2]:
        print("\n15m candles:")
        print(f"  Range: {range_15m[0]} to {range_15m[1]}")
        print(f"  Count: {range_15m[2]}")
        expected = 7 * 24 * 4
        coverage = range_15m[2] / expected * 100
        print(f"  Expected: {expected} (7 days * 4 candles/hour)")
        print(f"  Coverage: {coverage:.1f}%")

    # Sample prices
    sample = await conn.fetchrow(
        "SELECT open_price, high, low, close, volume FROM candles WHERE granularity='15m' ORDER BY timestamp DESC LIMIT 3"
    )
    if sample:
        print("\nSample (latest):")
        print(f"  Open: {sample[0]:.2f}")
        print(f"  High: {sample[1]:.2f}")
        print(f"  Low: {sample[2]:.2f}")
        print(f"  Close: {sample[3]:.2f}")
        print(f"  Volume: {sample[4]:.0f}")

    await conn.close()


asyncio.run(check())
