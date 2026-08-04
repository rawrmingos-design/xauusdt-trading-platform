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
import json
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
        """Poll candles until stopped (or max_cycles reached).

        Continuous semantics:
        - state (open position + equity counters) is restored from the store on
          startup and persisted after every processed batch
        - only candles not already recorded for this run are processed
        - run metadata (balance, drawdown, candles_processed) is updated
          incrementally; signals/orders/exits are appended, never replayed
        """
        self._running = True
        cycle = 0
        processed = (
            self._store.get_processed_candle_times(self._run_id)
            if self._store.run_exists(self._run_id)
            else set()
        )
        # restore live state (open position + equity) if this run was interrupted
        pos_state = self._store.get_position(self._run_id)
        if pos_state:
            self._harness.reset(self._run_id, self._commit)
            self._harness.restore_state(pos_state)
            if pos_state.get("last_processed_candle"):
                from datetime import datetime as _dt

                self._last_ts = _dt.fromisoformat(pos_state["last_processed_candle"]).timestamp()
            log.info(
                "Restored open position for run=%s side=%s qty=%.4f @ %.2f",
                self._run_id,
                pos_state.get("side"),
                pos_state.get("quantity", 0.0),
                pos_state.get("entry_price", 0.0),
            )
        else:
            self._harness.reset(self._run_id, self._commit)
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
                if not fresh:
                    log.debug("No fresh candles in this batch")
                    await asyncio.sleep(self._poll_interval)
                    continue
                # process only the new candles, preserving in-memory state
                for c in sorted(fresh, key=lambda x: x.open_time):
                    self._harness.process_candle_continuous(c)
                processed |= {c.open_time.isoformat() for c in fresh}

                # persist: position snapshot + run meta + new records
                batch = self._harness.drain_batch()
                if batch is not None:
                    self._store.append_records(batch)
                self._store.ensure_run(
                    self._run_id,
                    self._cfg.mode.value,
                    self._harness.strategy_version,
                    self._harness.config_hash,
                    self._commit,
                    self._cfg.initial_balance,
                )
                state = self._harness.harness_state()
                self._store.save_position(state)
                self._store.save_run_meta(
                    self._run_id,
                    self._harness.balance,
                    self._harness.peak_balance,
                    self._harness.max_drawdown,
                    self._harness.max_drawdown_pct,
                    self._harness.candles_processed,
                    fresh[-1].open_time.isoformat(),
                )
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

    @staticmethod
    def build_result_from_store(store: PaperStore, run_id: str) -> PaperRunResult | None:
        """Reconstruct a PaperRunResult from persisted continuous-run data.

        Used to generate daily reports for runs driven by the polling loop
        (which never builds a full result in memory).
        """
        run = store.get_run(run_id)
        if run is None:
            return None
        from xauusdt.backtest.models import Side
        from xauusdt.execution.paper.models import (
            SimExit,
            SimExitReason,
            SimMode,
            SimOrder,
            SimSignal,
        )

        signals = [
            SimSignal(
                run_id=run_id,
                candle_time=datetime.fromisoformat(s["candle_time"]),
                signal_type=s["signal_type"],
                side=Side(s["side"]),
                executed=bool(s["executed"]),
                buy_score=s["buy_score"],
                sell_score=s["sell_score"],
                entry_price=s["entry_price"],
                stop_loss_price=0.0,
                take_profit_price=0.0,
                partial_tp_price=0.0,
                context_adx=0.0,
                context_ema_trend="",
                context_structure="",
                context_conflict=False,
                strategy_version=s["strategy_version"],
                config_hash=s["config_hash"],
                commit_sha=s["commit_sha"],
                rejected=bool(s["rejected"]),
                rejection_reasons=json.loads(s["rejection_reasons"]),
            )
            for s in store.get_signals(run_id)
        ]
        orders = [
            SimOrder(
                run_id=run_id,
                candle_time=datetime.fromisoformat(o["candle_time"]),
                symbol=o["symbol"],
                side=Side(o["side"]),
                order_type=o["order_type"],
                price=o["price"],
                raw_price=o["raw_price"],
                quantity=o["quantity"],
                fee=o["fee"],
                status=o["status"],
            )
            for o in store.get_orders(run_id)
        ]
        exits = [
            SimExit(
                run_id=run_id,
                entry_candle_time=datetime.fromisoformat(e["entry_candle_time"]),
                exit_candle_time=datetime.fromisoformat(e["exit_candle_time"]),
                side=Side(e["side"]),
                exit_reason=SimExitReason(e["exit_reason"]),
                entry_price=e["entry_price"],
                exit_price=e["exit_price"],
                quantity=e["quantity"],
                pnl=e["pnl"],
                fee=e["fee"],
                is_partial=bool(e["is_partial"]),
                slippage_cost=e["slippage_cost"],
                sl_distance=e["sl_distance"],
            )
            for e in store.get_exits(run_id)
        ]
        return PaperRunResult(
            run_id=run_id,
            mode=SimMode(run["mode"]),
            strategy_version=run["strategy_version"],
            config_hash=run["config_hash"],
            commit_sha=run["commit_sha"],
            candles_processed=run["candles_processed"],
            signals=signals,
            orders=orders,
            exits=exits,
            final_balance=run["final_balance"],
            initial_balance=run["initial_balance"],
            max_drawdown=run["max_drawdown"],
            max_drawdown_pct=run["max_drawdown_pct"],
        )


def install_signal_handlers(runner: PaperRunner) -> None:
    """Install SIGINT/SIGTERM handlers for clean shutdown."""

    def _handler(signum: int, _frame: Any) -> None:
        log.info("Signal %d received, stopping runner...", signum)
        runner.stop()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
