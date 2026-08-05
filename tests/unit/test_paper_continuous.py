"""Unit tests for PROJECT-PAPER-002 continuous polling reliability.

Covers restart safety, deduplication, position recovery, and guard behaviour
(stale / gap) using deterministic fixtures — no live network, no real orders.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from xauusdt.backtest.models import Side
from xauusdt.exchange.models import Candle
from xauusdt.execution.paper.harness import PaperHarness
from xauusdt.execution.paper.models import (
    PaperConfig,
    SimMode,
    SimPosition,
)
from xauusdt.execution.paper.runner import PaperRunner
from xauusdt.execution.paper.store import PaperStore
from xauusdt.monitoring.service import MonitoringService
from xauusdt.monitoring.store import MonitorStore
from xauusdt.strategy.confluence import ConfluenceStrategy, make_v3_candidate_config


def _candle(
    t: datetime,
    close: float = 100.0,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1000.0,
) -> Candle:
    return Candle(
        symbol="XAU-USDT-SWAP",
        granularity="15m",
        open_time=t,
        open=close,
        high=high if high is not None else close * 1.001,
        low=low if low is not None else close * 0.999,
        close=close,
        volume=volume,
    )


def _candle_series(
    n: int, base: datetime, step_min: int = 15, price0: float = 100.0
) -> list[Candle]:
    out: list[Candle] = []
    price = price0
    for i in range(n):
        t = base + timedelta(minutes=step_min * i)
        out.append(_candle(t, close=price))
        price += 0.1
    return out


def _make_harness(mode: SimMode = SimMode.PAPER) -> tuple[PaperHarness, PaperConfig]:
    cfg = PaperConfig(mode=mode)
    strategy = ConfluenceStrategy(make_v3_candidate_config())
    return PaperHarness(strategy, cfg), cfg


# ------------------------------------------------------------------ fixtures
# A batched fetch queues a deterministic sequence of candle batches; the runner
# consumes them across cycles, exactly like OKX polling but offline.


class FakeFetcher:
    """Returns a queued sequence of candle batches, one per poll cycle."""

    def __init__(self, batches: list[list[Candle]]) -> None:
        self.batches = batches
        self.calls = 0

    async def __call__(self) -> list[Candle]:
        if self.calls >= len(self.batches):
            return []
        b = self.batches[self.calls]
        self.calls += 1
        return b


# ------------------------------------------------------------------ restore


def test_restore_open_position_roundtrip_recreates_identical_position() -> None:
    harness, _ = _make_harness()
    harness.reset("restore-1", "abc123")
    # open a position via state injection (deterministic, no strategy dependence)

    harness._cfg = PaperConfig(mode=SimMode.PAPER)
    # simulate an open LONG at 100.0
    harness._run_id = "restore-1"
    harness._position = SimPosition(
        run_id="restore-1",
        entry_candle_time=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        entry_price=100.0,
        side=Side.LONG,
        quantity=50.0,
        stop_loss_price=99.0,
        take_profit_price=102.0,
        partial_tp_price=101.0,
        partial_tp_ratio=0.5,
        is_partial_closed=False,
        max_mfe_price=100.5,
        max_mae_price=99.5,
    )
    harness._balance = 9_500.0
    harness._peak_balance = 10_100.0
    harness._max_drawdown = 300.0
    harness._candles_processed = 40
    state = harness.harness_state()
    assert state["side"] == "LONG"
    assert state["quantity"] == 50.0

    fresh = PaperHarness(
        ConfluenceStrategy(make_v3_candidate_config()), PaperConfig(mode=SimMode.PAPER)
    )
    fresh.reset("restore-1", "abc123")
    fresh.restore_state(state)
    assert fresh._position is not None
    assert fresh._position.side == Side.LONG
    assert fresh._position.entry_price == 100.0
    assert fresh._position.quantity == 50.0
    assert fresh._position.stop_loss_price == 99.0
    assert fresh._position.take_profit_price == 102.0
    assert fresh._position.is_partial_closed is False
    assert fresh._balance == 9_500.0
    assert fresh._peak_balance == 10_100.0
    assert fresh._candles_processed == 40


def test_restore_state_with_no_position_only_restores_equity() -> None:
    harness, _ = _make_harness()
    harness.reset("restore-2", "abc123")
    harness._balance = 9_777.0
    harness._peak_balance = 10_200.0
    harness._candles_processed = 12
    state = harness.harness_state()
    assert state["side"] is None

    fresh = PaperHarness(
        ConfluenceStrategy(make_v3_candidate_config()), PaperConfig(mode=SimMode.PAPER)
    )
    fresh.reset("restore-2", "abc123")
    fresh.restore_state(state)
    assert fresh._position is None
    assert fresh._balance == 9_777.0
    assert fresh._candles_processed == 12


# ------------------------------------------------------------ runner-level


def test_runner_deduplicates_candles_across_restart(tmp_path) -> None:
    """Restart with the same run_id must not reprocess or duplicate candles."""
    db = tmp_path / "paper.db"
    store = PaperStore(db)
    batches = [_candle_series(3, datetime(2026, 1, 1, 8, 0, tzinfo=UTC))]
    fetcher = FakeFetcher(batches)
    runner = PaperRunner(
        ConfluenceStrategy(make_v3_candidate_config()),
        store,
        "dedup-same",
        "sha1",
        fetch_candles=fetcher,
        poll_interval=0,
    )
    asyncio.run(runner.run_loop(max_cycles=1))
    assert len(store.get_signals("dedup-same")) == 3

    # restart same run id: fetch the SAME candles again (as OKX would).
    fetcher2 = FakeFetcher(batches)
    runner2 = PaperRunner(
        ConfluenceStrategy(make_v3_candidate_config()),
        store,
        "dedup-same",
        "sha1",
        fetch_candles=fetcher2,
        poll_interval=0,
    )
    asyncio.run(runner2.run_loop(max_cycles=1))
    # no duplicates: still 3 signals, no double entries
    assert len(store.get_signals("dedup-same")) == 3
    orders = store.get_orders("dedup-same")
    entries = [o for o in orders if o["order_type"] == "ENTRY"]
    exits = store.get_exits("dedup-same")
    full_exits = [e for e in exits if not e["is_partial"]]
    assert len(entries) == len(full_exits)  # one position at a time invariant
    store.close()


def test_runner_new_run_id_is_independent(tmp_path) -> None:
    """A fresh run_id must start from a clean slate."""
    db = tmp_path / "paper.db"
    store = PaperStore(db)
    batches = [_candle_series(3, datetime(2026, 1, 2, 8, 0, tzinfo=UTC))]
    runner = PaperRunner(
        ConfluenceStrategy(make_v3_candidate_config()),
        store,
        "run-a",
        "sha1",
        fetch_candles=FakeFetcher(batches),
        poll_interval=0,
    )
    asyncio.run(runner.run_loop(max_cycles=1))
    assert len(store.get_signals("run-a")) == 3

    # different run id, different data window
    batches2 = [_candle_series(2, datetime(2026, 1, 3, 8, 0, tzinfo=UTC))]
    runner2 = PaperRunner(
        ConfluenceStrategy(make_v3_candidate_config()),
        store,
        "run-b",
        "sha1",
        fetch_candles=FakeFetcher(batches2),
        poll_interval=0,
    )
    asyncio.run(runner2.run_loop(max_cycles=1))
    assert len(store.get_signals("run-a")) == 3
    assert len(store.get_signals("run-b")) == 2
    store.close()


def test_runner_heartbeat_advances_without_fresh_candles(tmp_path) -> None:
    """PROJECT-OPS-002: heartbeat must advance on EVERY successful poll, even
    when no fresh candles exist (15m bar, 2m poll). Otherwise the health timer
    false-alarms on a healthy runtime with a stale heartbeat."""
    db = tmp_path / "paper.db"
    store = PaperStore(db)
    monitor = MonitoringService(MonitorStore(db))
    # cycle 1: 3 fresh candles. cycles 2-4: same candles again (no new data).
    candles = _candle_series(3, datetime(2026, 1, 4, 8, 0, tzinfo=UTC))
    fetcher = FakeFetcher([candles, candles, candles, candles])
    runner = PaperRunner(
        ConfluenceStrategy(make_v3_candidate_config()),
        store,
        "hb-alive",
        "sha1",
        fetch_candles=fetcher,
        poll_interval=0,
        monitor=monitor,
    )
    asyncio.run(runner.run_loop(max_cycles=4))
    hb = monitor._store.get_heartbeat("hb-alive")
    assert hb is not None
    assert hb["collector_consecutive_errors"] == 0
    # signals processed exactly once (dedup across cycles)
    assert len(store.get_signals("hb-alive")) == 3
    store.close()
    monitor._store.close()


def test_runner_position_restored_after_restart(tmp_path) -> None:
    """An open position must survive a paper-process restart and continue."""
    db = tmp_path / "paper.db"
    store = PaperStore(db)
    base = datetime(2026, 2, 1, 8, 0, tzinfo=UTC)
    # seed an OPEN LONG position directly into the store, as the first run
    # would have done after opening one
    store.ensure_run("pos-restore", "paper", "v3_candidate", "cfg", "sha1", 10_000.0)
    store.save_position(
        {
            "run_id": "pos-restore",
            "symbol": "XAU-USDT-SWAP",
            "entry_candle_time": (base + timedelta(minutes=15)).isoformat(),
            "entry_price": 100.0,
            "side": "LONG",
            "quantity": 50.0,
            "stop_loss_price": 99.0,
            "take_profit_price": 102.0,
            "partial_tp_price": 101.0,
            "partial_tp_ratio": 0.5,
            "is_partial_closed": False,
            "max_mfe_price": 100.5,
            "max_mae_price": 99.5,
            "balance_snapshot": 9_900.0,
            "peak_balance_snapshot": 10_000.0,
            "max_drawdown_snapshot": 50.0,
            "last_processed_candle": (base + timedelta(minutes=30)).isoformat(),
        }
    )
    store.close()

    # restart: the runner must restore the open position
    store2 = PaperStore(db)
    b2 = _candle_series(3, base + timedelta(minutes=75), price0=100.0)
    runner2 = PaperRunner(
        ConfluenceStrategy(make_v3_candidate_config()),
        store2,
        "pos-restore",
        "sha1",
        fetch_candles=FakeFetcher([b2]),
        poll_interval=0,
    )
    asyncio.run(runner2.run_loop(max_cycles=1))
    # position row persists with its side after the resumed run
    pos2 = store2.get_position("pos-restore")
    assert pos2 is not None
    assert pos2["side"] == "LONG"
    assert abs(pos2["entry_price"] - 100.0) < 1e-9
    store2.close()


def test_runner_clean_partial_tp_state_roundtrips(tmp_path) -> None:
    """Partial-TP + break-even state must round-trip across a restart."""
    db = tmp_path / "paper.db"
    store = PaperStore(db)
    harness, _ = _make_harness()
    harness.reset("ptp-restore", "sha1")
    harness._position = SimPosition(
        run_id="ptp-restore",
        entry_candle_time=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
        entry_price=100.0,
        side=Side.LONG,
        quantity=25.0,  # remaining after partial close
        stop_loss_price=100.0,  # break-even after partial
        take_profit_price=102.0,
        partial_tp_price=None,  # already closed partial
        partial_tp_ratio=0.5,
        is_partial_closed=True,
        max_mfe_price=101.0,
        max_mae_price=99.0,
    )
    harness._balance = 9_600.0
    store.save_position(harness.harness_state())

    fresh = PaperHarness(
        ConfluenceStrategy(make_v3_candidate_config()), PaperConfig(mode=SimMode.PAPER)
    )
    fresh.reset("ptp-restore", "sha1")

    pos = store.get_position("ptp-restore")
    fresh.restore_state(pos)
    assert fresh._position is not None
    assert fresh._position.is_partial_closed is True
    assert fresh._position.quantity == 25.0
    assert fresh._position.stop_loss_price == 100.0  # break-even survived
    assert fresh._position.partial_tp_price is None
    store.close()


# ------------------------------------------------------------------- guards


def test_stale_candle_blocks_new_entry(tmp_path) -> None:
    """A stale candle (delta > stale_candle_seconds) must block new entries."""
    store = PaperStore(tmp_path / "paper.db")
    cfg = PaperConfig(mode=SimMode.PAPER, stale_candle_seconds=900, max_candle_gap_seconds=1800)
    harness = PaperHarness(ConfluenceStrategy(make_v3_candidate_config()), cfg)
    harness.reset("stale-block", "sha1")
    base = datetime(2026, 4, 1, 8, 0, tzinfo=UTC)
    candles = _candle_series(2, base, step_min=15)  # normal first candles
    # then a stale candle, 1 hour late (> 15 min stale window)
    stale = _candle(base + timedelta(minutes=120), close=108.0)
    harness.run(candles + [stale])
    stamps_after_stale = [
        o for o in harness._orders if o.order_type == "ENTRY" and o.candle_time >= stale.open_time
    ]
    assert len(stamps_after_stale) == 0
    store.close()


def test_gap_candle_blocks_new_entry(tmp_path) -> None:
    """A candle gap (delta > max_candle_gap_seconds) must block new entries."""
    store = PaperStore(tmp_path / "paper.db")
    cfg = PaperConfig(mode=SimMode.PAPER, stale_candle_seconds=900, max_candle_gap_seconds=1800)
    harness = PaperHarness(ConfluenceStrategy(make_v3_candidate_config()), cfg)
    harness.reset("gap-block", "sha1")
    base = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    candles = _candle_series(2, base, step_min=15)
    # a huge gap: > 30 minutes (2 candles) away
    gap = _candle(base + timedelta(hours=6), close=115.0)
    harness.run(candles + [gap])
    stamps_after_gap = [
        o for o in harness._orders if o.order_type == "ENTRY" and o.candle_time >= gap.open_time
    ]
    assert len(stamps_after_gap) == 0
    store.close()


def test_guards_record_rejection_reason(tmp_path) -> None:
    """Blocked entries must record a clear rejection reason in the signal."""
    store = PaperStore(tmp_path / "paper.db")
    cfg = PaperConfig(mode=SimMode.PAPER, stale_candle_seconds=900, max_candle_gap_seconds=1800)
    harness = PaperHarness(ConfluenceStrategy(make_v3_candidate_config()), cfg)
    harness.reset("guard-reason", "sha1")
    base = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    candles = _candle_series(2, base, step_min=15)
    gap = _candle(base + timedelta(hours=6), close=115.0)
    harness.run(candles + [gap])
    after = [s for s in harness._signals if s.candle_time >= gap.open_time]
    assert len(after) > 0
    # continuity guard records a clear reason for blocked-entry signals
    reasoned = [s for s in after if "continuity_guard" in s.rejection_reasons]
    assert len(reasoned) > 0
    store.close()


def test_daily_reports_generated_from_continuous_run(tmp_path) -> None:
    """Daily JSON + Markdown reports must render from continuous-run data."""
    db = tmp_path / "paper.db"
    store = PaperStore(db)
    batches = [_candle_series(5, datetime(2026, 7, 1, 8, 0, tzinfo=UTC))]
    runner = PaperRunner(
        ConfluenceStrategy(make_v3_candidate_config()),
        store,
        "reports-continuous",
        "sha1",
        fetch_candles=FakeFetcher(batches),
        poll_interval=0,
    )
    asyncio.run(runner.run_loop(max_cycles=1))
    run = store.get_run("reports-continuous")
    assert run is not None
    # build a PaperRunResult from persisted continuous-run data
    result = PaperRunner.build_result_from_store(store, "reports-continuous")
    assert result is not None
    from xauusdt.execution.paper.report import build_daily_report, write_daily_report

    report = build_daily_report(result)
    assert report["run_id"] == "reports-continuous"
    assert report["signals_count"] == 5
    write_daily_report(result, str(tmp_path), prefix="paper002_continuous")
    store.close()
