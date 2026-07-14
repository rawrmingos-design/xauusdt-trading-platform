"""Shared fixtures for PostgreSQL integration tests.

Requires TEST_DATABASE_URL environment variable pointing to a PostgreSQL
instance. If not set, tests are skipped with a clear message.

Default: ``postgresql+asyncpg://postgres:postgres@localhost:5432/xauusdt_test``
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from xauusdt.storage.models import Base

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/xauusdt_test",
)

# Skip marker for all tests in this directory
collect_ignore_glob: list[str] = []  # noqa: U101


def pytest_configure(config: Any) -> None:
    """Register skip_if_no_postgres marker."""
    config.addinivalue_line(
        "markers",
        "postgres: mark test as requiring PostgreSQL (skip if unavailable)",
    )


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


def pytest_runtest_setup(item: Any) -> None:
    """Skip tests in tests/integration/ that have postgres marker if DB unavailable."""
    if "test_candle_repository_postgres" not in str(item.module.__file__):
        return
    if not _can_connect(TEST_DB_URL):
        pytest.skip("PostgreSQL not available; set TEST_DATABASE_URL to enable")  # type: ignore[misc]


@asynccontextmanager
async def _create_test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create a one-shot engine for the test database."""
    url = TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql+asyncpg://")
    engine = create_async_engine(url, echo=False, pool_pre_ping=True, pool_size=5)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def event_loop() -> Any:  # noqa: ANN401
    """Create a single event loop for the session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def _ensure_test_db_exists() -> None:  # type: ignore[reportUnknownVariableType]
    """Ensure the test database exists, creating it if necessary."""
    # Connect to default 'postgres' database first
    default_url = TEST_DB_URL.rsplit("/", 1)[0].rsplit("/", 1)[0]  # strip db name
    default_url = f"{default_url}/postgres"

    default_engine = create_async_engine(default_url, echo=False)
    try:
        async with default_engine.connect() as conn:
            await conn.execute(text("COMMIT"))
            # Check if test DB exists
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = 'xauusdt_test'")
            )
            exists = result.scalar()
            if not exists:
                await conn.execute(text("CREATE DATABASE xauusdt_test"))
    finally:
        await default_engine.dispose()


@pytest.fixture(scope="session")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:  # type: ignore[reportUnknownVariableType]
    """Session-scoped engine for the test PostgreSQL database."""
    await _ensure_test_db_exists()

    async with _create_test_engine() as engine:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine


@pytest.fixture()
async def test_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session with automatic cleanup."""
    factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with factory() as session:
        yield session
        # Rollback any pending changes to keep tests isolated
        try:
            await session.rollback()
        except Exception:
            pass
