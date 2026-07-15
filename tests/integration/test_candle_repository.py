"""Tests for candle repository using SQLite in-memory database."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import close_db, create_tables, get_session, init_db


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


def _make_candle(
    symbol: str = "XAU-USDT-SWAP",
    granularity: str = "5m",
    open_time: datetime | None = None,
    open_: float = 3500.0,
    high: float = 3510.0,
    low: float = 3490.0,
    close: float = 3505.0,
    volume: float = 100.0,
    quote_volume: float = 350000.0,
) -> Candle:
    return Candle(
        symbol=symbol,
        granularity=granularity,
        open_time=open_time or datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        quote_volume=quote_volume,
    )


def _strip_tz(dt: datetime) -> datetime:
    """Strip timezone info for SQLite comparison."""
    return dt.replace(tzinfo=None)


def _safe_strip(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=None)


@pytest.fixture()
async def session():
    await init_db("sqlite+aiosqlite://")
    await create_tables()
    async for s in get_session():
        yield s
        break


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    await close_db()


class TestCandleRepository:
    async def test_insert_and_query(self, session: AsyncSession) -> None:
        repo = CandleRepository(session)
        candle = _make_candle(open_time=datetime(2026, 7, 14, 12, 0, tzinfo=UTC))
        await repo.insert(candle)
        await session.commit()

        results = await repo.query_by_range("XAU-USDT-SWAP", "5m")
        assert len(results) == 1
        assert results[0].symbol == "XAU-USDT-SWAP"
        assert _strip_tz(results[0].open_time) == datetime(2026, 7, 14, 12, 0)

    async def test_upsert_many_idempotent(self, session: AsyncSession) -> None:
        repo = CandleRepository(session)
        candles = [
            _make_candle(open_time=datetime(2026, 7, 14, 12, 0, tzinfo=UTC)),
            _make_candle(open_time=datetime(2026, 7, 14, 12, 5, tzinfo=UTC)),
        ]
        count1 = await repo.upsert_many(candles)
        await session.commit()
        assert count1 == 2

        count2 = await repo.upsert_many(candles)
        await session.commit()
        assert count2 == 0  # all skipped as duplicates

        total = await repo.count_in_range("XAU-USDT-SWAP", "5m")
        assert total == 2

    async def test_query_by_range_sorted(self, session: AsyncSession) -> None:
        repo = CandleRepository(session)
        early = datetime(2026, 7, 14, 11, 55, tzinfo=UTC)
        late = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        await repo.insert(_make_candle(open_time=late))
        await repo.insert(_make_candle(open_time=early))
        await session.commit()

        results = await repo.query_by_range("XAU-USDT-SWAP", "5m")
        assert [_strip_tz(c.open_time) for c in results] == [
            datetime(2026, 7, 14, 11, 55),
            datetime(2026, 7, 14, 12, 0),
        ]

    async def test_latest_open_time(self, session: AsyncSession) -> None:
        repo = CandleRepository(session)
        t1 = datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
        t2 = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        await repo.insert(_make_candle(open_time=t1))
        await repo.insert(_make_candle(open_time=t2))
        await session.commit()

        latest = await repo.latest_open_time("XAU-USDT-SWAP", "5m")
        assert _safe_strip(latest) == datetime(2026, 7, 14, 12, 0)

    async def test_count_in_range(self, session: AsyncSession) -> None:
        repo = CandleRepository(session)
        start = datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
        mid = datetime(2026, 7, 14, 11, 5, tzinfo=UTC)
        end = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        await repo.insert(_make_candle(open_time=start))
        await repo.insert(_make_candle(open_time=mid))
        await repo.insert(_make_candle(open_time=end))
        await session.commit()

        count = await repo.count_in_range("XAU-USDT-SWAP", "5m", start_time=start, end_time=end)
        assert count == 3
