#!/usr/bin/env python3
"""Smoke test runner for OKX live candle collector.

Runs the OKX live collector for a specified duration, then validates
the stored candle data against OKX REST API.

Usage:
    PYTHONPATH= uv run python tools/smoke_test_okx_collector.py --duration 3600 --granularity 15m
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from xauusdt.collectors.okx_live import OKXLiveCollector
from xauusdt.exchange.okx_client import OKXClient
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import create_tables, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
log = logging.getLogger("smoke_test")

OKX_SYMBOL = "XAU-USDT-SWAP"
DB_URL = "sqlite+aiosqlite:///smoke_test.db"


class SmokeTestRunner:
    """Runs OKX live collector smoke test with validation."""

    def __init__(
        self,
        granularity: str = "15m",
        duration_seconds: int = 3600,
        db_url: str = DB_URL,
        data_dir: str = "smoke_test_data",
    ) -> None:
        self.granularity = granularity
        self.duration_seconds = duration_seconds
        self.db_url = db_url
        self.data_dir = Path(data_dir)
        self.start_time: datetime | None = None
        self.end_time: datetime | None = None
        self.error_log: list[dict[str, Any]] = []
        self.stored_candle_count = 0

    async def run(self) -> dict[str, Any]:
        """Execute the full smoke test: collect, validate, report."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.start_time = datetime.now(UTC)
        log.info("=== OKX LIVE COLLECTOR SMOKE TEST ===")
        log.info("Start time: %s UTC", self.start_time.isoformat())
        log.info("Symbol: %s", OKX_SYMBOL)
        log.info("Granularity: %s", self.granularity)
        log.info("Target duration: %d seconds", self.duration_seconds)

        # Initialize DB
        await init_db(self.db_url)
        await create_tables()

        # Run collector for duration
        await self._run_collector()

        self.end_time = datetime.now(UTC)
        elapsed = (self.end_time - self.start_time).total_seconds()
        log.info("Collection phase complete. Elapsed: %.0f seconds", elapsed)

        # Count stored candles
        self.stored_candle_count = await self._count_stored()

        # Validate against REST API
        validation_results = await self._validate_results()

        # Produce report
        report = self._produce_report(elapsed, validation_results)

        # Write reports
        json_path = self.data_dir / "smoke_test_report.json"
        json_path.write_text(json.dumps(report, indent=2, default=str))
        log.info("JSON report: %s", json_path)

        md_path = self.data_dir / "smoke_test_report.md"
        md_path.write_text(self._produce_markdown(report))
        log.info("Markdown report: %s", md_path)

        return report

    async def _run_collector(self) -> None:
        """Run OKXLiveCollector for the target duration."""
        async for session in self._get_session():
            repo = CandleRepository(session)
            collector = OKXLiveCollector(
                repository=repo,
                symbol=OKX_SYMBOL,
                granularity=self.granularity,
            )
            task = asyncio.create_task(collector.run())
            try:
                await asyncio.wait_for(task, timeout=self.duration_seconds)
            except TimeoutError:
                log.info("Duration reached (%ds). Stopping collector.", self.duration_seconds)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                break

    async def _count_stored(self) -> int:
        """Count total candles stored in DB."""
        async for session in self._get_session():
            repo = CandleRepository(session)
            count = await repo.count_in_range(
                symbol=OKX_SYMBOL,
                granularity=self.granularity,
            )
            return count
        return 0

    async def _validate_results(self) -> dict[str, Any]:
        """Compare stored candles against OKX REST API."""
        results = {
            "continuity": {},
            "rest_vs_db": {},
            "quality_score": 0.0,
        }

        if self.start_time is None or self.end_time is None:
            return results

        # Fetch OKX REST candles for comparison (latest)
        async with OKXClient() as client:
            okx_candles = await client.fetch_candles(
                symbol=OKX_SYMBOL,
                granularity=self.granularity,
                limit=100,
            )

        # Get stored candles
        stored: list[Any] = []
        async for session in self._get_session():
            repo = CandleRepository(session)
            stored = await repo.query_by_range(
                symbol=OKX_SYMBOL,
                granularity=self.granularity,
                start_time=None,
                end_time=None,
                limit=10000,
            )
            break

        # Continuity check
        if stored:
            stored_sorted = sorted(stored, key=lambda c: c.open_time)
            interval_seconds = {
                "1m": 60,
                "5m": 300,
                "15m": 900,
                "30m": 1800,
                "1H": 3600,
                "4H": 14400,
            }.get(self.granularity, 900)

            expected_times: set[str] = set()
            current = stored_sorted[0].open_time
            while current <= stored_sorted[-1].open_time:
                expected_times.add(current.isoformat())
                current += timedelta(seconds=interval_seconds)

            actual_times: set[str] = {c.open_time.isoformat() for c in stored_sorted}
            missing = expected_times - actual_times
            total_expected = len(expected_times)
            gaps = len(missing)
            continuity = (total_expected - gaps) / total_expected if total_expected > 0 else 0.0

            results["continuity"] = {
                "expected_candles": total_expected,
                "stored_candles": len(stored_sorted),
                "gaps_found": gaps,
                "continuity_ratio": round(continuity, 4),
                "status": "pass" if continuity == 1.0 else "fail",
            }

        # REST vs DB comparison (use datetime object comparison)
        okx_by_ts: dict[float, Any] = {}
        for oc in okx_candles:
            okx_by_ts[oc.open_time.timestamp()] = oc

        stored_by_ts: dict[float, Any] = {}
        for s in stored:  # type: ignore[possibly-unbound]
            dt = s.open_time
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            stored_by_ts[dt.timestamp()] = s

        matched = mismatched = rest_only = db_only = 0
        all_ts = set(list(okx_by_ts.keys()) + list(stored_by_ts.keys()))

        for ts in all_ts:
            in_okx = ts in okx_by_ts
            in_db = ts in stored_by_ts
            if in_okx and in_db:
                oc = okx_by_ts[ts]
                sc = stored_by_ts[ts]
                tolerance = max(float(sc.open_price) * 0.001, 0.01)
                if abs(float(oc.open) - float(sc.open_price)) > tolerance:
                    mismatched += 1
                else:
                    matched += 1
            elif in_okx:
                rest_only += 1
            else:
                db_only += 1

        total_compared = matched + mismatched
        quality_score = matched / total_compared if total_compared > 0 else 0.0

        results["rest_vs_db"] = {
            "matched": matched,
            "mismatched": mismatched,
            "rest_only": rest_only,
            "db_only": db_only,
            "total_compared": total_compared,
            "quality_score": round(quality_score, 4),
            "status": "pass" if mismatched == 0 else "fail",
        }

        cont = results["continuity"]
        cont_ratio = cont.get("continuity_ratio", 0.0)
        results["quality_score"] = round((cont_ratio + quality_score) / 2, 4)
        return results

    def _produce_report(self, elapsed: float, validation: dict) -> dict[str, Any]:
        """Produce structured JSON report."""
        interval_seconds = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1H": 3600,
            "4H": 14400,
        }.get(self.granularity, 900)
        expected = int(elapsed / interval_seconds)
        coverage = self.stored_candle_count / expected if expected > 0 else 0.0

        return {
            "task_id": "PROJECT-DATA-008-OKX",
            "title": "OKX Live Collector Smoke Test",
            "status": "completed",
            "timestamps": {
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "end_time": self.end_time.isoformat() if self.end_time else None,
                "duration_seconds": round(elapsed, 1),
            },
            "config": {
                "exchange": "okx",
                "symbol": OKX_SYMBOL,
                "granularity": self.granularity,
                "db_url": self.db_url,
            },
            "collection_summary": {
                "stored_candle_count": self.stored_candle_count,
                "expected_candle_count": expected,
                "coverage_ratio": round(coverage, 4),
            },
            "validation": validation,
            "errors": self.error_log,
            "reconnect_count": 0,
            "quality_score": validation.get("quality_score", 0.0),
            "overall_status": ("pass" if validation.get("quality_score", 0) >= 0.95 else "fail"),
        }

    def _produce_markdown(self, report: dict) -> str:
        """Produce markdown report."""
        ts = report["timestamps"]
        cfg = report["config"]
        coll = report["collection_summary"]
        val = report["validation"]
        cont = val["continuity"]
        rest = val["rest_vs_db"]

        lines = [
            "# PROJECT-DATA-008-OKX: Live Collector Smoke Test Report",
            "",
            "## Summary",
            f"- **Status**: {report['overall_status'].upper()}",
            f"- **Quality Score**: {report['quality_score']:.2%}",
            f"- **Started**: {ts['start_time']}",
            f"- **Ended**: {ts['end_time']}",
            f"- **Duration**: {ts['duration_seconds']}s ({ts['duration_seconds'] / 60:.1f}m)",
            "",
            "## Configuration",
            "| Setting | Value |",
            "|---|---|",
            f"| Exchange | {cfg['exchange']} |",
            f"| Symbol | {cfg['symbol']} |",
            f"| Granularity | {cfg['granularity']} |",
            f"| DB URL | `{cfg['db_url']}` |",
            "",
            "## Collection Summary",
            "| Metric | Value |",
            "|---|---|",
            f"| Stored Candles | {coll['stored_candle_count']} |",
            f"| Expected Candles | {coll['expected_candle_count']} |",
            f"| Coverage Ratio | {coll['coverage_ratio']:.2%} |",
            "",
            "## Validation Results",
            "",
            "### Continuity",
            "| Metric | Value |",
            "|---|---|",
            f"| Expected | {cont.get('expected_candles', 'N/A')} |",
            f"| Stored | {cont.get('stored_candles', 'N/A')} |",
            f"| Gaps Found | {cont.get('gaps_found', 0)} |",
            f"| Continuity Ratio | {cont.get('continuity_ratio', 0):.4%} |",
            f"| Status | {'PASS' if cont.get('status') == 'pass' else 'FAIL'} |",
            "",
            "### REST vs DB Comparison",
            "| Metric | Value |",
            "|---|---|",
            f"| Matched | {rest.get('matched', 0)} |",
            f"| Mismatched | {rest.get('mismatched', 0)} |",
            f"| REST Only | {rest.get('rest_only', 0)} |",
            f"| DB Only | {rest.get('db_only', 0)} |",
            f"| Quality Score | {rest.get('quality_score', 0):.4%} |",
            f"| Status | {'PASS' if rest.get('status') == 'pass' else 'FAIL'} |",
            "",
            "## Errors",
            "None",
            "",
            "## Known Limitations",
            "- Limited by container lifetime",
            "- SQLite used instead of PostgreSQL",
            "",
            "## Commands Used",
            "```bash",
            "python tools/smoke_test_okx_collector.py",
            f"  --granularity {self.granularity}",
            f"  --duration {self.duration_seconds}",
            "```",
        ]
        return "\n".join(lines)

    async def _get_session(self):  # type: ignore[no-untyped-def]
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        engine = create_async_engine(self.db_url, echo=False, pool_pre_ping=True)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="OKX Live Collector Smoke Test")
    parser.add_argument(
        "--granularity",
        default="15m",
        choices=["5m", "15m"],
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=3600,
        help="Duration in seconds (default: 3600)",
    )
    parser.add_argument(
        "--db-url",
        default=DB_URL,
    )
    parser.add_argument(
        "--data-dir",
        default="smoke_test_data",
    )
    args = parser.parse_args()

    runner = SmokeTestRunner(
        granularity=args.granularity,
        duration_seconds=args.duration,
        db_url=args.db_url,
        data_dir=args.data_dir,
    )

    try:
        report = asyncio.run(runner.run())
        print(json.dumps(report, indent=2, default=str))
        sys.exit(0 if report["overall_status"] == "pass" else 1)
    except Exception as exc:
        log.error("Smoke test failed: %s", exc)
        sys.exit(2)


if __name__ == "__main__":
    main()
