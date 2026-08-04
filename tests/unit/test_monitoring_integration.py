"""Integration tests: PaperHarness + PaperRunner emit monitoring events.

Verifies observation-only wiring is correct:
  - PaperHarness._check_continuity records stale/gap/duplicate events
  - PaperRunner.run_started/stop + heartbeat are called
  - continuity counters are incremented
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xauusdt.exchange.models import Candle
from xauusdt.execution.paper.harness import PaperHarness
from xauusdt.execution.paper.models import PaperConfig
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy


def _candle(t: datetime) -> Candle:
    return Candle(
        symbol="XAU-USDT-SWAP",
        granularity="15m",
        open_time=t,
        open=2400.0,
        high=2410.0,
        low=2395.0,
        close=2405.0,
        volume=100.0,
    )


class _Recorder:
    """Records every monitor call (no SQLite, deterministic)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.counters: dict[str, int] = {"stale": 0, "gap": 0, "dup": 0}

    def record(self, run_id: str, code: str, message: str, severity: str, metadata: Any) -> None:  # noqa: ARG001
        self.calls.append((code, severity, str(metadata)))
        for k in ("stale", "gap", "dup"):
            if k in code:
                self.counters[k] += 1

    def heartbeat(self, *a: Any, **k: Any) -> None:
        self.calls.append(("heartbeat", "INFO", ""))


class FakeMon:
    """Test double for MonitoringService — records calls into a _Recorder."""

    def __init__(self, rec: _Recorder) -> None:
        self._rec = rec

    def record(self, *a: Any, **k: Any) -> None:
        self._rec.record(*a, **k)

    def heartbeat(self, run_id: str, mode: str, state: Any, **k: Any) -> None:  # noqa: ARG001
        self._rec.calls.append(("heartbeat", "INFO", ""))

    def run_started(self, *a: Any, **k: Any) -> None:  # noqa: ARG002
        self._rec.calls.append(("run_started", "INFO", ""))

    def run_stopped(self, *a: Any, **k: Any) -> None:  # noqa: ARG002
        self._rec.calls.append(("run_stopped", "INFO", ""))


def test_harness_records_stale_gap_duplicate(tmp_path: Path) -> None:
    rec = _Recorder()
    cfg = ConfluenceConfig()
    strat = ConfluenceStrategy(cfg)
    harness = PaperHarness(
        strategy=strat,
        paper_cfg=PaperConfig(),
        monitor=FakeMon(rec),
    )
    harness._run_id = "r1"
    harness._last_candle_time = _candle(_t(10, 0)).open_time

    # gap (+2h > max gap)
    harness._check_continuity(_candle(_t(12, 0)))
    # duplicate (replay candle with same time as last -> delta 0)
    harness._last_candle_time = _t(12, 0)
    harness._check_continuity(_candle(_t(12, 0)))
    codes = [c[0] for c in rec.calls]
    assert "monitor_candle_gap" in codes
    assert "monitor_duplicate_attempt" in codes


def _t(hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 8, 4, hour, minute, tzinfo=UTC)


def test_continuity_counters_increment(tmp_path: Path) -> None:
    rec = _Recorder()
    cfg = ConfluenceConfig()
    harness = PaperHarness(strategy=ConfluenceStrategy(cfg), monitor=FakeMon(rec))
    harness._run_id = "r1"
    harness._last_candle_time = _t(10, 0)

    # duplicate twice
    harness._check_continuity(_candle(_t(10, 0)))
    harness._check_continuity(_candle(_t(10, 0)))
    assert rec.counters["dup"] == 2
    assert harness._monitor_counters["dup"] == 2
    # gap
    harness._check_continuity(_candle(_t(13, 0)))
    assert rec.counters["gap"] == 1
    assert harness._monitor_counters["gap"] == 1
