"""Async SQLAlchemy repository for candle persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from xauusdt.storage.models import CandleOrm


class CandleRepository:
    """Async repository for candle CRUD with idempotent upsert."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, candle: Any) -> CandleOrm:
        """Insert a single candle; raises on duplicate unique constraint."""
        orm = CandleOrm.from_candle(candle)
        self._session.add(orm)
        await self._session.flush()
        return orm

    async def upsert_many(self, candles: list[Any]) -> int:
        """Upsert multiple candles idempotently.

        Uses ON CONFLICT DO UPDATE for PostgreSQL.
        Falls back to serial insert for SQLite without upsert support.
        Returns number of rows affected.

        Chunks PostgreSQL inserts to avoid 32767 parameter limit.
        """
        if not candles:
            return 0

        dialect = self._session.bind.dialect.name if self._session.bind else "postgresql"
        if dialect == "sqlite":
            return await self._upsert_sqlite(candles)
        return await self._upsert_postgres(candles)

    async def _upsert_postgres(self, candles: list[Any]) -> int:
        """PostgreSQL upsert using ON CONFLICT ... DO UPDATE.

        Chunks the list to avoid PostgreSQL 32767 parameter limit.
        """
        if not candles:
            return 0

        # PostgreSQL max params is 32767. Each row has ~11 columns, so ~3000 rows safe.
        chunk_size = 1000
        total_stored = 0

        for i in range(0, len(candles), chunk_size):
            chunk = candles[i : i + chunk_size]
            total_stored += await self._do_upsert_postgres_chunk(chunk)

        return total_stored

    async def _do_upsert_postgres_chunk(self, candles: list[Any]) -> int:
        """PostgreSQL upsert for a single chunk of candles."""
        from sqlalchemy.dialects.postgresql import insert

        stmt = insert(CandleOrm).values(
            [
                {
                    "id": str(uuid4()),
                    "symbol": c.symbol,
                    "granularity": c.granularity,
                    "open_time": c.open_time,
                    "close_time": c.close_time,
                    "open_price": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                    "quote_volume": c.quote_volume,
                }
                for c in candles
            ]
        )

        stmt = stmt.on_conflict_do_update(
            constraint="uq_candle_symbol_granularity_time",
            set_={
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "quote_volume": stmt.excluded.quote_volume,
                "updated_at": datetime.now(UTC),
            },
        )

        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount  # type: ignore[attr-defined, no-any-return]

    async def _upsert_sqlite(self, candles: list[Any]) -> int:
        """SQLite upsert fallback: skip existing, insert new."""
        existing = await self._list_existing(candles)
        inserted = 0
        for candle in candles:
            key = (candle.symbol, candle.granularity, candle.open_time)
            if key in existing:
                continue
            orm = CandleOrm.from_candle(candle)
            self._session.add(orm)
            inserted += 1
        await self._session.flush()
        await self._session.commit()
        return inserted

    async def _list_existing(self, candles: list[Any]) -> set[tuple[str, str, datetime]]:
        """Return set of (symbol, granularity, open_time) for existing candles."""
        if not candles:
            return set()
        keys = {(c.symbol, c.granularity, c.open_time) for c in candles}
        symbols = list({k[0] for k in keys})
        granularities = list({k[1] for k in keys})
        stmt = select(CandleOrm.symbol, CandleOrm.granularity, CandleOrm.open_time).where(
            CandleOrm.symbol.in_(symbols),
            CandleOrm.granularity.in_(granularities),
        )
        result = await self._session.execute(stmt)
        existing: set[tuple[str, str, datetime]] = set()
        for row in result.all():
            existing.add((row[0], row[1], row[2].replace(tzinfo=UTC)))
        return existing

    async def query_by_range(
        self,
        symbol: str,
        granularity: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[CandleOrm]:
        """Query candles by symbol, granularity, and optional time range."""
        stmt = select(CandleOrm).where(
            CandleOrm.symbol == symbol,
            CandleOrm.granularity == granularity,
        )
        if start_time:
            stmt = stmt.where(CandleOrm.open_time >= start_time)
        if end_time:
            stmt = stmt.where(CandleOrm.open_time <= end_time)
        stmt = stmt.order_by(CandleOrm.open_time.asc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def latest_open_time(self, symbol: str, granularity: str) -> datetime | None:
        """Return the latest stored open_time for the symbol/granularity."""
        stmt = select(func.max(CandleOrm.open_time)).where(
            CandleOrm.symbol == symbol, CandleOrm.granularity == granularity
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_in_range(
        self,
        symbol: str,
        granularity: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> int:
        """Count candles in a time range."""
        stmt = (
            select(func.count())
            .select_from(CandleOrm)
            .where(
                CandleOrm.symbol == symbol,
                CandleOrm.granularity == granularity,
            )
        )
        if start_time:
            stmt = stmt.where(CandleOrm.open_time >= start_time)
        if end_time:
            stmt = stmt.where(CandleOrm.open_time <= end_time)
        result = await self._session.execute(stmt)
        return result.scalar_one()
