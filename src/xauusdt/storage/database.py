"""Async database session management for candle storage."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from xauusdt.config import Settings


def _build_engine(database_url: str) -> AsyncEngine:
    """Create async SQLAlchemy engine with sensible defaults."""
    poolclass = None
    if "sqlite" in database_url and ":memory:" in database_url:
        poolclass = StaticPool
    return create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        poolclass=poolclass,
    )


def _build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


engine: AsyncEngine | None = None
session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db(database_url: str | None = None) -> None:
    """Initialize engine and session factory."""
    global engine, session_factory
    url = database_url or Settings().db_url
    engine = _build_engine(url)
    session_factory = _build_session_factory(engine)


async def create_tables() -> None:
    """Create all tables defined in ORM models."""
    if not engine:
        raise RuntimeError("Database not initialized; call init_db() first")
    from xauusdt.storage.models import Base  # noqa: PLC0415

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session for use in FastAPI or standalone code."""
    if not session_factory:
        raise RuntimeError("Database not initialized; call init_db() first")
    async with session_factory() as session:
        yield session


async def close_db() -> None:
    """Dispose engine and clear factory."""
    global engine, session_factory
    if engine:
        await engine.dispose()
        engine = None
    session_factory = None
