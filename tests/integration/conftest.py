"""Shared fixtures for PostgreSQL integration tests.

Requires ``TEST_DATABASE_URL`` environment variable pointing to a PostgreSQL
instance. If not set, tests are skipped with a clear message.

Default: ``postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt_test``.
In CI the variable is set automatically by the workflow; the step
"Create test database" must run before ``uv run pytest``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

# ------------------------------------------------------------------
# Database URL
# ------------------------------------------------------------------

TEST_DB_URL: str = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt_test",
)


def _can_connect(url: str) -> bool:
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
    config._pg_available = _pg_available


def pytest_runtest_setup(item: Any) -> None:
    """Skip PostgreSQL tests if database is unavailable."""
    if "test_candle_repository_postgres" not in str(item.module.__file__):
        return
    pg_available = getattr(item.config, "_pg_available", False)
    if not pg_available:
        pytest.skip("PostgreSQL not available; set TEST_DATABASE_URL to enable")


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_engine() -> AsyncEngine:
    """Session-scoped PostgreSQL engine."""
    engine = create_async_engine(TEST_DB_URL, echo=False, pool_pre_ping=True)
    yield engine
    asyncio.get_event_loop().run_until_complete(engine.dispose())


@pytest.fixture(scope="session")
def pg_session(pg_engine: AsyncEngine) -> AsyncSession:
    """Session-scoped PostgreSQL session."""
    from xauusdt.storage.database import create_tables, init_db

    async def _setup() -> AsyncSession:
        await init_db(TEST_DB_URL)
        await create_tables()

        # Reset schema state
        async with pg_engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS candle_sticks CASCADE"))
            await conn.commit()

        async with pg_engine.connect() as conn:
            session = AsyncSession(conn, autoflush=False)
            return session

    return asyncio.get_event_loop().run_until_complete(_setup())
