"""Create xauusdt_test database for integration tests."""

from __future__ import annotations

import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


async def main() -> None:
    """Create the test database if it doesn't exist."""
    engine: AsyncEngine = create_async_engine(
        "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt",
        echo=False,
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("COMMIT"))
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = 'xauusdt_test'")
            )
            exists = result.scalar()
            if exists:
                print("xauusdt_test already exists, skipping creation.")
                return
            await conn.execute(text("CREATE DATABASE xauusdt_test"))
            print("Created xauusdt_test database.")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR creating database: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    import asyncio  # noqa: PLC0415

    asyncio.run(main())
