"""Candle-driven runner for the shadow/paper harness (PROJECT-PAPER-001).

Two entry points:
  - `run_once(candles)`: deterministic single-pass evaluation (tests, replay, CLI --once)
  - `run_loop(...)`: polling mode that fetches new candles via OKX REST
    (validated collector), processes them, persists state, and stops
    cleanly on SIGINT/SIGTERM.

Idempotency: `store.get_processed_candle_times(run_id)` is used to skip
candles already recorded; a signal with the same (run_id, candle_time)
is not written twice. Restarting resumes at the first unprocessed candle.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime
from typing import Any

from xauusdt.exchange.models import Candle
from xauusdt.execution.paper.harness import DEFAULT_GRANULARITY, DEFAULT_SYMBOL, PaperHarness
from xauusdt.execution.paper.models import PaperConfig, PaperRunResult
from xauusdt.execution.paper.store import PaperStore
from xauusdt.strategy.confluence import ConfluenceStrategy

log = logging.getLogger(__name__)


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


async def run_once(
    harness: PaperHarness,
    store: PaperStore,
    candles: list[Candle],
    run_id: str,
    commit: str,
    resume: bool = True,
) -> PaperRunResult:
    """Deterministic single-pass evaluation. Skips already-processed candles when resume=True."""
    processed = (
        store.get_processed_candle_times(run_id) if resume and store.run_exists(run_id) else set()
    )
    fresh = [c for c in candles if c.open_time.isoformat() not in processed]
    harness.reset(run_id, commit)
    result = harness.run(fresh)
    store.save_run(result)
    return result


class PaperRunner:
    """Polling loop: fetch new candles -> process -> persist -> sleep."""

    def __init__(
        self,
        strategy: ConfluenceStrategy,
        store: PaperStore,
        run_id: str,
        commit: str,
        paper_cfg: PaperConfig | None = None,
        fetch_candles: Any | None = None,
        poll_interval: int = 120,
    ) -> None:
        self._strategy = strategy
        self._store = store
        self._run_id = run_id
        self._commit = commit
        self._cfg = paper_cfg or PaperConfig()
        self._harness = PaperHarness(strategy, self._cfg)
        self._fetch = fetch_candles
        self._poll_interval = poll_interval
        self._running = False
        self._last_ts: float = 0.0

    async def run_loop(self, max_cycles: int | None = None) -> None:
        """Poll candles until stopped (or max_cycles reached)."""
        self._running = True
        cycle = 0
        processed = (
            self._store.get_processed_candle_times(self._run_id)
            if self._store.run_exists(self._run_id)
            else set()
        )
        log.info(
            "PaperRunner started run=%s mode=%s resume_candles=%d",
            self._run_id,
            self._cfg.mode.value,
            len(processed),
        )
        while self._running:
            if max_cycles is not None and cycle >= max_cycles:
                log.info("Reached max_cycles=%d. Stopping.", max_cycles)
                break
            cycle += 1
            try:
                if self._fetch is None:
                    from xauusdt.exchange.okx_client import OKXClient

                    async with OKXClient() as client:
                        candles = await client.fetch_candles(
                            symbol=DEFAULT_SYMBOL,
                            granularity=DEFAULT_GRANULARITY,
                            limit=5,
                        )
                else:
                    candles = await self._fetch()
                if not candles:
                    log.debug("No candles returned")
                    await asyncio.sleep(self._poll_interval)
                    continue
                newest_ts = max(c.open_time.timestamp() for c in candles)
                if newest_ts <= self._last_ts:
                    log.debug("No new candles since last poll")
                    await asyncio.sleep(self._poll_interval)
                    continue
                self._last_ts = newest_ts
                fresh = [c for c in candles if c.open_time.isoformat() not in processed]
                if fresh:
                    self._harness.reset(self._run_id, self._commit)
                    # replay from last known position state is handled by store resume;
                    # simplest correct behavior: reprocess fresh candles each cycle
                    result = self._harness.run(fresh)
                    self._store.save_run(result)
                    processed |= {c.open_time.isoformat() for c in fresh}
                    log.info(
                        "Processed %d new candles (latest %s)",
                        len(fresh),
                        fresh[-1].open_time.isoformat(),
                    )
            except asyncio.CancelledError:
                log.info("PaperRunner cancelled")
                raise
            except Exception:
                log.exception("Error in poll cycle")
            await asyncio.sleep(self._poll_interval)
        self._running = False

    def stop(self) -> None:
        self._running = False


def install_signal_handlers(runner: PaperRunner) -> None:
    """Install SIGINT/SIGTERM handlers for clean shutdown."""

    def _handler(signum: int, _frame: Any) -> None:
        log.info("Signal %d received, stopping runner...", signum)
        runner.stop()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
