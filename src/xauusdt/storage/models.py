"""SQLAlchemy ORM models for candle storage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime, Float, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class CandleOrm(Base):
    """Candle table mapped to PostgreSQL."""

    __tablename__ = "candles"

    id = Column(String(36), primary_key=True, default=lambda: _uuid4())
    symbol = Column(String(20), nullable=False, index=True)
    granularity = Column(String(10), nullable=False)
    open_time = Column(DateTime(timezone=True), nullable=False, index=True)
    close_time = Column(DateTime(timezone=True), nullable=False)
    open_price = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    quote_volume = Column(Float, nullable=False, default=0.0)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint(
            "symbol", "granularity", "open_time", name="uq_candle_symbol_granularity_time"
        ),
    )

    @classmethod
    def from_candle(cls, candle: Any) -> CandleOrm:
        """Convert domain Candle to ORM model."""
        return cls(
            symbol=candle.symbol,
            granularity=candle.granularity,
            open_time=candle.open_time,
            close_time=candle.close_time,
            open_price=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            quote_volume=candle.quote_volume,
        )


def _uuid4() -> str:
    import uuid

    return str(uuid.uuid4())
