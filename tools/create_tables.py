"""Create candle storage tables in PostgreSQL."""

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt",
)

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS candles (
    id VARCHAR(36) PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    granularity VARCHAR(10) NOT NULL,
    open_time TIMESTAMP WITH TIME ZONE NOT NULL,
    close_time TIMESTAMP WITH TIME ZONE NOT NULL,
    open_price DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    quote_volume DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_candle_symbol_granularity_time UNIQUE (symbol, granularity, open_time)
);
"""

INDEX_SQL_1 = "CREATE INDEX IF NOT EXISTS idx_candles_symbol ON candles (symbol);"
INDEX_SQL_2 = "CREATE INDEX IF NOT EXISTS idx_candles_open_time ON candles (open_time);"


async def main() -> None:
    engine = create_async_engine(DB_URL, echo=False)
    try:
        async with engine.connect() as conn:
            await conn.execute(text(TABLE_SQL))
            await conn.commit()
            await conn.execute(text(INDEX_SQL_1))
            await conn.commit()
            await conn.execute(text(INDEX_SQL_2))
            await conn.commit()
            print("Tables and indexes created successfully")

        # Verify
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
            tables = [row[0] for row in result]
            print(f"Tables: {tables}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
