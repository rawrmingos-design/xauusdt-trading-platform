"""Tests for tools/compare_candles.py — REST vs DB comparison with mocked data."""

from __future__ import annotations

import pytest

from tools.compare_candles import (
    fields_approx_match,
    human_readable,
    parse_args,
    parse_iso8601,
)


class TestParseArgs:
    def test_defaults(self) -> None:
        args = parse_args(
            [
                "--symbol",
                "XAUUSDT_UMCBL",
                "--granularity",
                "15m",
                "--start-time",
                "2026-07-14T00:00:00Z",
                "--end-time",
                "2026-07-14T06:00:00Z",
                "--db-url",
                "sqlite+aiosqlite:///test.db",
                "--output",
                "json",
            ]
        )
        assert args.symbol == "XAUUSDT_UMCBL"
        assert args.granularity == "15m"
        assert args.output == "json"
        assert args.tolerance == pytest.approx(0.00000001)

    def test_custom_tolerance(self) -> None:
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
                "--tolerance",
                "0.001",
            ]
        )
        assert args.tolerance == pytest.approx(0.001)


class TestParseISO8601:
    def test_z_suffix(self) -> None:
        dt = parse_iso8601("2026-07-14T00:00:00Z")
        assert dt.hour == 0
        assert dt.tzinfo is not None


class TestFieldsApproxMatch:
    def test_all_match(self) -> None:
        db = {"open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 100.0}
        rest = {"open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 100.0}
        ok, mismatches = fields_approx_match(db, rest, 0.0001)
        assert ok is True
        assert mismatches == []

    def test_single_mismatch(self) -> None:
        db = {"open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 100.0}
        rest = {"open": 100.0, "high": 110.01, "low": 95.0, "close": 105.0, "volume": 100.0}
        ok, mismatches = fields_approx_match(db, rest, 0.0001)
        assert ok is False
        assert len(mismatches) == 1
        assert "high" in mismatches[0]

    def test_within_tolerance(self) -> None:
        db = {"open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 100.0}
        rest = {"open": 100.0, "high": 110.000000005, "low": 95.0, "close": 105.0, "volume": 100.0}
        ok, mismatches = fields_approx_match(db, rest, 0.00000001)
        assert ok is True

    def test_missing_field(self) -> None:
        db = {"open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 100.0}
        rest = {"open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0}  # missing volume
        ok, mismatches = fields_approx_match(db, rest, 0.0001)
        assert ok is False
        assert any("volume" in m for m in mismatches)

    def test_both_none(self) -> None:
        db = {"open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 100.0}
        rest = {"open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": None}
        ok, mismatches = fields_approx_match(db, rest, 0.0001)
        assert ok is False

    def test_multiple_mismatches(self) -> None:
        db = {"open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 100.0}
        rest = {
            "open": 101.0,
            "high": 109.0,
            "low": 96.0,
            "close": 104.0,
            "volume": 90.0,
        }
        ok, mismatches = fields_approx_match(db, rest, 0.0001)
        assert ok is False
        assert len(mismatches) == 5


class TestHumanReadable:
    def test_passed_report(self) -> None:
        report = {
            "symbol": "X",
            "granularity": "15m",
            "start_time": "2026-07-14T00:00:00Z",
            "end_time": "2026-07-14T06:00:00Z",
            "rest_count": 24,
            "db_count": 24,
            "matched": 24,
            "missing_in_db": 0,
            "extra_in_db": 0,
            "mismatched": 0,
            "mismatches": [],
            "status": "passed",
        }
        output = human_readable(report)
        assert "PASSED" in output
        assert "Matched:      24" in output

    def test_failed_report_with_mismatches(self) -> None:
        report = {
            "symbol": "X",
            "granularity": "15m",
            "start_time": "2026-07-14T00:00:00Z",
            "end_time": "2026-07-14T06:00:00Z",
            "rest_count": 24,
            "db_count": 23,
            "matched": 20,
            "missing_in_db": 1,
            "extra_in_db": 0,
            "mismatched": 3,
            "mismatches": [
                {
                    "open_time": "2026-07-14T02:00:00+00:00",
                    "details": ["high: DB=110.0 REST=110.5"],
                },
            ],
            "status": "failed",
        }
        output = human_readable(report)
        assert "FAILED" in output
        assert "110.5" in output

    def test_failed_report_with_error(self) -> None:
        report = {
            "symbol": "X",
            "granularity": "15m",
            "status": "failed",
            "error": "Connection refused",
            "rest_count": 0,
            "db_count": 0,
            "matched": 0,
            "missing_in_db": 0,
            "mismatched": 0,
        }
        output = human_readable(report)
        assert "Connection refused" in output


class TestValidationReportFields:
    def test_missing_in_db_count(self) -> None:
        """missing_in_db should count timestamps in REST but not in DB."""
        report = {
            "symbol": "X",
            "granularity": "15m",
            "start_time": "2026-07-14T00:00:00Z",
            "end_time": "2026-07-14T01:00:00Z",
            "rest_count": 4,
            "db_count": 3,
            "matched": 3,
            "missing_in_db": 1,
            "extra_in_db": 0,
            "mismatched": 0,
            "mismatches": [],
            "status": "failed",
        }
        output = human_readable(report)
        assert "Missing in DB: 1" in output

    def test_extra_in_db_shown(self) -> None:
        report = {
            "symbol": "X",
            "granularity": "1H",
            "start_time": "2026-07-14T00:00:00Z",
            "end_time": "2026-07-14T05:00:00Z",
            "rest_count": 5,
            "db_count": 6,
            "matched": 5,
            "missing_in_db": 0,
            "extra_in_db": 1,
            "mismatched": 0,
            "mismatches": [],
            "status": "warning",
        }
        output = human_readable(report)
        assert "Extra in DB:   1" in output
        assert "WARNING" in output
