"""Shared fixtures for PostgreSQL integration tests.

Requires TEST_DATABASE_URL environment variable pointing to a PostgreSQL
instance. If not set, tests are skipped with a clear message.

Default: `postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt_test`.
In CI the variable is set automatically by the workflow; the step
"Create test database" must run before ``uv run pytest``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, create_async_engine

if TYPE_CHECKING:
    pass  # noqa: ERA001


# ------------------------------------------------------------------
# Database URL from environment or CI defaults
# ------------------------------------------------------------------

TEST_DB_URL: str = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt_test",
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _can_connect(url: str) -> bool:  # type: ignore[reportUnknownVariableType]
    """Check if a PostgreSQL connection is possible."""
    try:
        loop = asyncio.new_event_loop()
        engine = create_async_engine(url, pool_pre_ping=True)

        async def _check() -> bool:
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                    return True
            except Exception:
                return False
            finally:
                await engine.dispose()

        result = loop.run_until_complete(_check())
        loop.close()
        return result
    except Exception:
        return False


# ------------------------------------------------------------------
# Pytest hooks
# ------------------------------------------------------------------


def pytest_configure(config: Any) -> None:
    """Register marker and cache PG availability."""
    config.addinivalue_line(
        "markers",
        "postgres: mark test as requiring PostgreSQL (skip if unavailable)",
    )
    _pg_available = _can_connect(TEST_DB_URL)
    config._pg_available = _pg_available  # type: ignore[attr-defined]


def pytest_runtest_setup(item: Any) -> None:
    """Skip PostgreSQL tests if database is unavailable."""
    if "test_candle_repository_postgres" not in str(item.module.__file__):
        return
    pg_available = getattr(item.config, "_pg_available", False)
    if not pg_available:
        pytest.skip("PostgreSQL not available; set TEST_DATABASE_URL to enable")  # type: ignore[misc]


# ------------------------------------------------------------------
# Session-scoped engine and session fixtures
# ------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_engine() -> AsyncEngine:  # type: ignore[reportUnknownVariableType]
    """Create a session-scoped PostgreSQL engine."""
    engine = create_async_engine(TEST_DB_URL, echo=False, pool_pre_ping=True)
    yield engine
    asyncio.get_event_loop().run_until_complete(engine.dispose())


@pytest.fixture(scope="session")
def test_session(test_engine: AsyncEngine) -> AsyncSession:  # type: ignore[reportUnknownVariableType]
    """Create a session-scoped PostgreSQL session."""
    from xauusdt.storage.database import init_db, create_tables  # noqa: PLC0415

    async def _setup() -> AsyncSession:
        await init_db(TEST_DB_URL)
        await create_tables()
        async with test_engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS candle_sticks CASCADE"))
            await conn.execute(text("CREATE TABLE IF NOT EXISTS candle_sticks (id SERIAL PRIMARY KEY)"))
            await conn.commit()
        async with test_engine.begin() as conn:
            await conn.execute(text("DELETE FROM candle_sticks"))
            await conn.commit()
        # Create a session from the engine directly
        async with test_engine.connect() as conn:
            from xauusdt.storage.models import CandleStick  # noqa: PLC0415
            session = AsyncSession(conn, autoflush=False)
            return session

    return asyncio.get_event_loop().run_until_complete(_setup())  # type: ignore[return-value]
