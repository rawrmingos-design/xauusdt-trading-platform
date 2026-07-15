"""PostgreSQL integration tests for CandleRepository.

Tests run against a real PostgreSQL database to verify production-like
behavior for unique constraints, idempotent upserts, timezone handling,
and range queries.

Requires:
    export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/xauusdt_test

To run locally:
    uv run pytest tests/integration/test_candle_repository_postgres.py -v

To run in CI (PostgreSQL service already configured in .github/workflows/ci.yml):
    uv run pytest tests/integration/test_candle_repository_postgres.py -v
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository

TEST_SYMBOL = "XAU-USDT-SWAP"
TEST_GRANULARITY = "15m"
BASE_TIME = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)


def _make_candle(open_offset_minutes: int = 0) -> Candle:
    """Create a test candle with open_time offset from BASE_TIME."""
    open_time = BASE_TIME
    return Candle(
        symbol=TEST_SYMBOL,
        granularity=TEST_GRANULARITY,
        open_time=open_time,
        open=3500.0 + open_offset_minutes,
        high=3510.0 + open_offset_minutes,
        low=3490.0 + open_offset_minutes,
        close=3505.0 + open_offset_minutes,
        volume=100.0 + open_offset_minutes,
        quote_volume=350500.0 + open_offset_minutes * 100,
    )


def _make_candle_with_time(open_time: datetime) -> Candle:
    return Candle(
        symbol=TEST_SYMBOL,
        granularity=TEST_GRANULARITY,
        open_time=open_time,
        open=3500.0,
        high=3510.0,
        low=3490.0,
        close=3505.0,
        volume=100.0,
        quote_volume=350500.0,
    )


class TestCandleInsertPostgres:
    """Test CandleRepository.insert against PostgreSQL."""

    @pytest.mark.asyncio
    async def test_insert_single_candle(self, pg_session: AsyncSession) -> None:
        """Insert a single candle via repository."""
        candle = _make_candle()
        repo = CandleRepository(pg_session)
        orm = await repo.insert(candle)
        await pg_session.commit()

        assert orm.id is not None
        assert orm.symbol == TEST_SYMBOL
        assert orm.open_time == candle.open_time

        # Verify it exists in DB
        result = await pg_session.execute(
            text("SELECT COUNT(*) FROM candles WHERE symbol = :sym"),
            {"sym": TEST_SYMBOL},
        )
        assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_insert_duplicate_raises(self, pg_session: AsyncSession) -> None:
        """Inserting a duplicate candle raises unique constraint error."""
        candle = _make_candle()
        repo = CandleRepository(pg_session)

        # First insert succeeds
        await repo.insert(candle)
        await pg_session.commit()

        # Second insert with same key raises IntegrityError
        candle2 = _make_candle()
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await repo.insert(candle2)
            await pg_session.commit()


class TestUpsertManyPostgres:
    """Test CandleRepository.upsert_many idempotency on PostgreSQL."""

    @pytest.mark.asyncio
    async def test_upsert_many_first_time(self, pg_session: AsyncSession) -> None:
        """First upsert inserts all new candles."""
        candles = [_make_candle(i) for i in range(10)]
        repo = CandleRepository(pg_session)
        count = await repo.upsert_many(candles)
        await pg_session.commit()

        assert count == 10

        result = await pg_session.execute(
            text("SELECT COUNT(*) FROM candles WHERE symbol = :sym"),
            {"sym": TEST_SYMBOL},
        )
        assert result.scalar() == 10

    @pytest.mark.asyncio
    async def test_upsert_many_idempotent_rerun(self, pg_session: AsyncSession) -> None:
        """Rerunning upsert with same candles does not create duplicates."""
        candles = [_make_candle(i) for i in range(5)]
        repo = CandleRepository(pg_session)

        # First upsert
        count1 = await repo.upsert_many(candles)
        await pg_session.commit()
        assert count1 == 5

        # Second upsert (same candles)
        count2 = await repo.upsert_many(candles)
        await pg_session.commit()
        assert count2 == 5  # ON CONFLICT DO UPDATE updates, does not insert

        result = await pg_session.execute(
            text("SELECT COUNT(*) FROM candles WHERE symbol = :sym"),
            {"sym": TEST_SYMBOL},
        )
        assert result.scalar() == 5  # No duplicates

    @pytest.mark.asyncio
    async def test_upsert_many_updates_existing_deterministically(
        self, pg_session: AsyncSession
    ) -> None:
        """Updating existing candles deterministically modifies the row."""
        candles = [_make_candle(i) for i in range(3)]
        repo = CandleRepository(pg_session)

        # First upsert
        await repo.upsert_many(candles)
        await pg_session.commit()

        # Upsert with modified values
        updated_candles = [
            Candle(
                symbol=TEST_SYMBOL,
                granularity=TEST_GRANULARITY,
                open_time=candle.open_time,
                open=9999.0,  # Changed value
                high=9999.0,
                low=9999.0,
                close=9999.0,
                volume=9999.0,
                quote_volume=9999000.0,
            )
            for candle in candles
        ]
        count = await repo.upsert_many(updated_candles)
        await pg_session.commit()
        assert count == 3

        # Verify values were updated
        result = await pg_session.execute(
            text(
                "SELECT open, high, low, close, volume FROM candles "
                "WHERE symbol = :sym AND open_time = :ot"
            ),
            {"sym": TEST_SYMBOL, "ot": candles[1].open_time},
        )
        row = result.fetchone()
        assert row is not None
        assert abs(row.open - 9999.0) < 0.001
        assert abs(row.close - 9999.0) < 0.001

    @pytest.mark.asyncio
    async def test_upsert_many_mixed_new_and_existing(self, pg_session: AsyncSession) -> None:
        """Upsert with mix of new and existing candles."""
        # Insert 3 candles first
        existing = [_make_candle(i) for i in range(3)]
        repo = CandleRepository(pg_session)
        await repo.upsert_many(existing)
        await pg_session.commit()

        # Upsert with 2 existing + 2 new
        new_candles = [
            _make_candle(0),  # exists
            _make_candle(1),  # exists
            _make_candle(3),  # new
            _make_candle(4),  # new
        ]
        count = await repo.upsert_many(new_candles)
        await pg_session.commit()
        assert count == 4  # PostgreSQL returns affected rows (updates + inserts)

        result = await pg_session.execute(
            text("SELECT COUNT(*) FROM candles WHERE symbol = :sym"),
            {"sym": TEST_SYMBOL},
        )
        assert result.scalar() == 5  # 3 existing + 2 new


class TestUniqueConstraintPostgres:
    """Test that PostgreSQL unique constraint is enforced."""

    @pytest.mark.asyncio
    async def test_unique_constraint_enforced(self, pg_session: AsyncSession) -> None:
        """PostgreSQL rejects duplicate (symbol, granularity, open_time)."""
        candle = _make_candle()
        repo = CandleRepository(pg_session)

        await repo.insert(candle)
        await pg_session.commit()

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            duplicate = Candle(
                symbol=candle.symbol,
                granularity=candle.granularity,
                open_time=candle.open_time,
                open=0.0,
                high=0.0,
                low=0.0,
                close=0.0,
                volume=0.0,
            )
            await repo.insert(duplicate)
            await pg_session.commit()

    @pytest.mark.asyncio
    async def test_unique_constraint_allows_different_symbol(
        self, pg_session: AsyncSession
    ) -> None:
        """Same granularity + open_time but different symbol is allowed."""
        candle1 = Candle(
            symbol="XAU-USDT-SWAP",
            granularity="15m",
            open_time=BASE_TIME,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=100.0,
        )
        candle2 = Candle(
            symbol="XAGUSDT_UMCBL",  # Different symbol
            granularity="15m",
            open_time=BASE_TIME,  # Same time
            open=200.0,
            high=200.0,
            low=200.0,
            close=200.0,
            volume=200.0,
        )
        repo = CandleRepository(pg_session)

        await repo.insert(candle1)
        await repo.insert(candle2)
        await pg_session.commit()  # Should not raise

        result = await pg_session.execute(
            text("SELECT COUNT(*) FROM candles WHERE open_time = :ot"),
            {"ot": BASE_TIME},
        )
        assert result.scalar() == 2


class TestTimezonePostgres:
    """Test timezone-aware UTC datetime handling."""

    @pytest.mark.asyncio
    async def test_utc_timestamps_preserved(self, pg_session: AsyncSession) -> None:
        """UTC timestamps are stored and retrieved correctly."""
        exact_time = datetime(2025, 6, 15, 12, 30, 0, tzinfo=UTC)
        candle = _make_candle_with_time(exact_time)
        repo = CandleRepository(pg_session)

        await repo.insert(candle)
        await pg_session.commit()

        # Retrieve directly from DB
        result = await pg_session.execute(
            text("SELECT open_time FROM candles WHERE symbol = :sym AND open_time = :ot"),
            {"sym": TEST_SYMBOL, "ot": exact_time},
        )
        row = result.fetchone()
        assert row is not None
        # SQLAlchemy returns timezone-aware datetime for PostgreSQL TIMESTAMPTZ
        assert row.open_time.tzinfo is not None

    @pytest.mark.asyncio
    async def test_timezone_aware_in_repo_query(self, pg_session: AsyncSession) -> None:
        """CandleRepository.query_by_range works with timezone-aware datetimes."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        middle = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 2, 0, tzinfo=UTC)

        # Insert candles at different times
        candles = [
            Candle(
                symbol=TEST_SYMBOL,
                granularity="1H",
                open_time=start,
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                volume=100.0,
            ),
            Candle(
                symbol=TEST_SYMBOL,
                granularity="1H",
                open_time=middle,
                open=200.0,
                high=200.0,
                low=200.0,
                close=200.0,
                volume=200.0,
            ),
            Candle(
                symbol=TEST_SYMBOL,
                granularity="1H",
                open_time=end,
                open=300.0,
                high=300.0,
                low=300.0,
                close=300.0,
                volume=300.0,
            ),
        ]
        repo = CandleRepository(pg_session)
        await repo.upsert_many(candles)
        await pg_session.commit()

        # Query range [start, middle]
        results = await repo.query_by_range(TEST_SYMBOL, "1H", start_time=start, end_time=middle)
        assert len(results) == 2

        # Query range [start, end)
        results2 = await repo.query_by_range(TEST_SYMBOL, "1H", start_time=start, end_time=end)
        assert len(results2) == 2


class TestQueryByRangePostgres:
    """Test CandleRepository.query_by_range on PostgreSQL."""

    @pytest.mark.asyncio
    async def test_query_by_range_sorted(self, pg_session: AsyncSession) -> None:
        """query_by_range returns candles sorted ascending by open_time."""
        candles = [
            Candle(
                symbol=TEST_SYMBOL,
                granularity="15m",
                open_time=BASE_TIME.replace(minute=30),
                open=30.0,
                high=30.0,
                low=30.0,
                close=30.0,
                volume=30.0,
            ),
            Candle(
                symbol=TEST_SYMBOL,
                granularity="15m",
                open_time=BASE_TIME,
                open=0.0,
                high=0.0,
                low=0.0,
                close=0.0,
                volume=0.0,
            ),
            Candle(
                symbol=TEST_SYMBOL,
                granularity="15m",
                open_time=BASE_TIME.replace(minute=15),
                open=15.0,
                high=15.0,
                low=15.0,
                close=15.0,
                volume=15.0,
            ),
        ]
        repo = CandleRepository(pg_session)
        await repo.upsert_many(candles)
        await pg_session.commit()

        results = await repo.query_by_range(
            TEST_SYMBOL,
            TEST_GRANULARITY,
            start_time=BASE_TIME,
            end_time=BASE_TIME.replace(hour=1),
        )
        times = [r.open_time for r in results]
        assert times == sorted(times)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_query_by_range_filtering(self, pg_session: AsyncSession) -> None:
        """query_by_range filters by symbol and granularity."""
        candles = [
            Candle(
                symbol="XAU-USDT-SWAP",
                granularity="15m",
                open_time=BASE_TIME,
                open=10.0,
                high=10.0,
                low=10.0,
                close=10.0,
                volume=10.0,
            ),
            Candle(
                symbol="XAGUSDT_UMCBL",  # Different symbol
                granularity="15m",
                open_time=BASE_TIME,
                open=20.0,
                high=20.0,
                low=20.0,
                close=20.0,
                volume=20.0,
            ),
        ]
        repo = CandleRepository(pg_session)
        await repo.upsert_many(candles)
        await pg_session.commit()

        results = await repo.query_by_range(
            "XAU-USDT-SWAP", "15m", start_time=BASE_TIME, end_time=BASE_TIME.replace(hour=1)
        )
        assert len(results) == 1
        assert results[0].symbol == "XAU-USDT-SWAP"


class TestLatestOpenTimePostgres:
    """Test CandleRepository.latest_open_time on PostgreSQL."""

    @pytest.mark.asyncio
    async def test_latest_open_time_returns_max(self, pg_session: AsyncSession) -> None:
        """latest_open_time returns the maximum open_time for the symbol/granularity."""
        candles = [
            Candle(
                symbol=TEST_SYMBOL,
                granularity=TEST_GRANULARITY,
                open_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                open=0.0,
                high=0.0,
                low=0.0,
                close=0.0,
                volume=0.0,
            ),
            Candle(
                symbol=TEST_SYMBOL,
                granularity=TEST_GRANULARITY,
                open_time=datetime(2025, 1, 1, 2, 0, tzinfo=UTC),
                open=2.0,
                high=2.0,
                low=2.0,
                close=2.0,
                volume=2.0,
            ),
            Candle(
                symbol=TEST_SYMBOL,
                granularity=TEST_GRANULARITY,
                open_time=datetime(2025, 1, 1, 1, 0, tzinfo=UTC),
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1.0,
            ),
        ]
        repo = CandleRepository(pg_session)
        await repo.upsert_many(candles)
        await pg_session.commit()

        latest = await repo.latest_open_time(TEST_SYMBOL, TEST_GRANULARITY)
        assert latest == datetime(2025, 1, 1, 2, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_latest_open_time_none_when_empty(self, pg_session: AsyncSession) -> None:
        """latest_open_time returns None when no candles exist."""
        repo = CandleRepository(pg_session)
        latest = await repo.latest_open_time("NONEXISTENT", "5m")
        assert latest is None


class TestCountInRangePostgres:
    """Test CandleRepository.count_in_range on PostgreSQL."""

    @pytest.mark.asyncio
    async def test_count_in_range(self, pg_session: AsyncSession) -> None:
        """count_in_range returns correct count for a time range."""
        candles = [
            Candle(
                symbol=TEST_SYMBOL,
                granularity=TEST_GRANULARITY,
                open_time=datetime(2025, 1, 1, h, 0, tzinfo=UTC),
                open=float(h),
                high=float(h),
                low=float(h),
                close=float(h),
                volume=float(h),
            )
            for h in [0, 1, 2, 3, 4]
        ]
        repo = CandleRepository(pg_session)
        await repo.upsert_many(candles)
        await pg_session.commit()

        # Count [0, 3]
        count = await repo.count_in_range(
            TEST_SYMBOL,
            TEST_GRANULARITY,
            start_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            end_time=datetime(2025, 1, 1, 3, 0, tzinfo=UTC),
        )
        assert count == 4  # 0, 1, 2, 3

        # Count all
        count_all = await repo.count_in_range(TEST_SYMBOL, TEST_GRANULARITY)
        assert count_all == 5

    @pytest.mark.asyncio
    async def test_count_in_range_empty(self, pg_session: AsyncSession) -> None:
        """count_in_range returns 0 when no candles match."""
        repo = CandleRepository(pg_session)
        count = await repo.count_in_range(
            "NONEXISTENT",
            "5m",
            start_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            end_time=datetime(2025, 1, 1, 1, 0, tzinfo=UTC),
        )
        assert count == 0
