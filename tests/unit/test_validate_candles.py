"""Tests for tools/validate_candles.py — gap detection, duplicates, continuity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.validate_candles import (
    GRANULARITY_DELTA,
    GRANULARITY_SECONDS,
    parse_args,
    parse_iso8601,
    validate,
)


class TestParseArgs:
    def test_defaults(self) -> None:
        args = parse_args(
            [
                "--symbol",
                "XAU-USDT-SWAP",
                "--granularity",
                "15m",
                "--start-time",
                "2026-07-14T00:00:00Z",
                "--end-time",
                "2026-07-14T06:00:00Z",
                "--db-url",
                "sqlite+aiosqlite:///test.db",
            ]
        )
        assert args.symbol == "XAU-USDT-SWAP"
        assert args.granularity == "15m"
        assert args.output == "text"

    def test_json_output(self) -> None:
        args = parse_args(
            [
                "--symbol",
                "X",
                "--granularity",
                "5m",
                "--start-time",
                "2026-01-01T00:00:00Z",
                "--end-time",
                "2026-01-01T01:00:00Z",
                "--db-url",
                "x",
                "--output",
                "json",
            ]
        )
        assert args.output == "json"


class TestParseISO8601:
    def test_z_suffix(self) -> None:
        dt = parse_iso8601("2026-07-14T00:00:00Z")
        assert dt.hour == 0
        assert dt.minute == 0
        assert dt.tzinfo is not None

    def test_plus_offset(self) -> None:
        dt = parse_iso8601("2026-07-14T00:00:00+00:00")
        assert dt.hour == 0


class TestGranularityConstants:
    def test_all_keys_have_delta(self) -> None:
        for gran in ["1m", "5m", "15m", "30m", "1H", "4H", "1D"]:
            assert gran in GRANULARITY_DELTA
            assert gran in GRANULARITY_SECONDS
            assert GRANULARITY_SECONDS[gran] > 0

    def test_15m_delta(self) -> None:
        assert GRANULARITY_DELTA["15m"] == timedelta(minutes=15)

    def test_1d_seconds(self) -> None:
        assert GRANULARITY_SECONDS["1D"] == 86400


class TestValidationUnsupportedGranularity:
    @pytest.mark.asyncio
    async def test_unsupported_granularity(self) -> None:
        args = parse_args(
            [
                "--symbol",
                "X",
                "--granularity",
                "invalid",
                "--start-time",
                "2026-01-01T00:00:00Z",
                "--end-time",
                "2026-01-01T01:00:00Z",
                "--db-url",
                "x",
            ]
        )
        report = await validate(args)
        assert report["status"] == "failed"
        assert "granularity" in report.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_start_after_end(self) -> None:
        args = parse_args(
            [
                "--symbol",
                "X",
                "--granularity",
                "1H",
                "--start-time",
                "2026-01-01T10:00:00Z",
                "--end-time",
                "2026-01-01T05:00:00Z",
                "--db-url",
                "x",
            ]
        )
        report = await validate(args)
        assert report["status"] == "failed"
        assert "start-time" in report.get("error", "").lower()


def _build_mock_engine(candles: list[Any]) -> MagicMock:
    """Build a mock engine that returns the given candles from a query."""
    orm_candle = MagicMock()
    orm_candle.symbol = "X"
    orm_candle.granularity = "15m"
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = candles
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_session_obj = MagicMock()
    mock_session_obj.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_obj.__aexit__ = AsyncMock(return_value=None)

    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()
    return mock_engine, mock_session_obj


class TestValidationFull:
    def _make_orm(self, open_time: datetime) -> MagicMock:
        orm = MagicMock()
        orm.symbol = "X"
        orm.granularity = "15m"
        orm.open_time = open_time
        return orm

    @pytest.mark.asyncio
    async def test_passed_no_gaps_no_duplicates(self) -> None:
        """Full coverage — all expected candles present, no duplicates."""
        start = datetime(2026, 7, 14, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 7, 14, 1, 0, 0, tzinfo=UTC)  # 4 x 15m
        candles = [self._make_orm(start + timedelta(minutes=15 * i)) for i in range(4)]

        mock_engine, mock_session_obj = _build_mock_engine(candles)

        with (
            patch(
                "tools.validate_candles.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "tools.validate_candles.AsyncSession",
                return_value=mock_session_obj,
            ),
        ):
            args = parse_args(
                [
                    "--symbol",
                    "X",
                    "--granularity",
                    "15m",
                    "--start-time",
                    start.isoformat(),
                    "--end-time",
                    end.isoformat(),
                    "--db-url",
                    "sqlite+aiosqlite:///t.db",
                ]
            )
            report = await validate(args)

        assert report["status"] == "passed"
        assert report["expected_count"] == 4
        assert report["actual_count"] == 4
        assert report["missing_count"] == 0
        assert report["duplicate_count"] == 0
        assert report["gaps"] == []

    @pytest.mark.asyncio
    async def test_warning_one_gap(self) -> None:
        """Missing one interval out of 4."""
        start = datetime(2026, 7, 14, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 7, 14, 1, 0, 0, tzinfo=UTC)

        # 00:00 and 00:30 — missing 00:15 and 00:45
        candles = [
            self._make_orm(start),
            self._make_orm(start + timedelta(minutes=30)),
        ]

        mock_engine, mock_session_obj = _build_mock_engine(candles)

        with (
            patch(
                "tools.validate_candles.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "tools.validate_candles.AsyncSession",
                return_value=mock_session_obj,
            ),
        ):
            args = parse_args(
                [
                    "--symbol",
                    "X",
                    "--granularity",
                    "15m",
                    "--start-time",
                    start.isoformat(),
                    "--end-time",
                    end.isoformat(),
                    "--db-url",
                    "sqlite+aiosqlite:///t.db",
                ]
            )
            report = await validate(args)

        assert report["status"] == "warning"
        assert report["missing_count"] == 2
        assert len(report["gaps"]) > 0

    @pytest.mark.asyncio
    async def test_failed_with_duplicates(self) -> None:
        """Duplicate open_time → failed."""
        start = datetime(2026, 7, 14, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 7, 14, 1, 0, 0, tzinfo=UTC)

        dup = self._make_orm(start)
        candles = [dup, dup, self._make_orm(start + timedelta(minutes=15))]

        mock_engine, mock_session_obj = _build_mock_engine(candles)

        with (
            patch(
                "tools.validate_candles.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "tools.validate_candles.AsyncSession",
                return_value=mock_session_obj,
            ),
        ):
            args = parse_args(
                [
                    "--symbol",
                    "X",
                    "--granularity",
                    "15m",
                    "--start-time",
                    start.isoformat(),
                    "--end-time",
                    end.isoformat(),
                    "--db-url",
                    "sqlite+aiosqlite:///t.db",
                ]
            )
            report = await validate(args)

        assert report["status"] == "failed"
        assert report["duplicate_count"] == 1
        assert len(report["duplicates"]) > 0

    @pytest.mark.asyncio
    async def test_empty_result_all_gaps(self) -> None:
        """Zero candles in DB → all gaps → warning."""
        start = datetime(2026, 7, 14, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 7, 14, 1, 0, 0, tzinfo=UTC)

        mock_engine, mock_session_obj = _build_mock_engine([])

        with (
            patch(
                "tools.validate_candles.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "tools.validate_candles.AsyncSession",
                return_value=mock_session_obj,
            ),
        ):
            args = parse_args(
                [
                    "--symbol",
                    "X",
                    "--granularity",
                    "15m",
                    "--start-time",
                    start.isoformat(),
                    "--end-time",
                    end.isoformat(),
                    "--db-url",
                    "sqlite+aiosqlite:///t.db",
                ]
            )
            report = await validate(args)

        assert report["status"] == "warning"
        assert report["expected_count"] == 4
        assert report["actual_count"] == 0
        assert report["missing_count"] == 4
        assert len(report["gaps"]) > 0

    @pytest.mark.asyncio
    async def test_consecutive_gaps_grouped(self) -> None:
        """Consecutive missing intervals grouped into one gap."""
        start = datetime(2026, 7, 14, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 7, 14, 2, 0, 0, tzinfo=UTC)  # 8 x 15m

        # Only 00:00 and 01:30 — 00:15-01:15 missing (5 consecutive) + 01:45 (1)
        candles = [
            self._make_orm(start),
            self._make_orm(start + timedelta(minutes=90)),
        ]

        mock_engine, mock_session_obj = _build_mock_engine(candles)

        with (
            patch(
                "tools.validate_candles.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "tools.validate_candles.AsyncSession",
                return_value=mock_session_obj,
            ),
        ):
            args = parse_args(
                [
                    "--symbol",
                    "X",
                    "--granularity",
                    "15m",
                    "--start-time",
                    start.isoformat(),
                    "--end-time",
                    end.isoformat(),
                    "--db-url",
                    "sqlite+aiosqlite:///t.db",
                ]
            )
            report = await validate(args)

        assert report["status"] == "warning"
        assert report["missing_count"] == 6
        # Gaps: 00:15-01:15 (5 consecutive) + 01:45 (isolated) = 2 gaps
        assert len(report["gaps"]) == 2

    @pytest.mark.asyncio
    async def test_all_missing_grouped_single_gap(self) -> None:
        """All missing intervals grouped into one contiguous gap."""
        start = datetime(2026, 7, 14, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 7, 14, 2, 0, 0, tzinfo=UTC)  # 8 x 15m

        # No candles at all — all 8 missing, consecutive
        mock_engine, mock_session_obj = _build_mock_engine([])

        with (
            patch(
                "tools.validate_candles.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "tools.validate_candles.AsyncSession",
                return_value=mock_session_obj,
            ),
        ):
            args = parse_args(
                [
                    "--symbol",
                    "X",
                    "--granularity",
                    "15m",
                    "--start-time",
                    start.isoformat(),
                    "--end-time",
                    end.isoformat(),
                    "--db-url",
                    "sqlite+aiosqlite:///t.db",
                ]
            )
            report = await validate(args)

        assert report["status"] == "warning"
        assert report["missing_count"] == 8
        # All 8 consecutive → 1 gap
        assert len(report["gaps"]) == 1
