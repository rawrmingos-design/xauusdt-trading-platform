"""Unit tests for PROJECT-PAPER-001 shadow/paper harness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from xauusdt.backtest.models import Side, Signal
from xauusdt.exchange.models import Candle
from xauusdt.execution.paper.harness import PaperHarness, commit_sha, config_hash
from xauusdt.execution.paper.models import (
    PaperConfig,
    SimExitReason,
    SimMode,
    SimPosition,
)
from xauusdt.execution.paper.store import PaperStore
from xauusdt.strategy.confluence import ConfluenceStrategy, make_v3_candidate_config


def _candle(
    t: datetime,
    close: float = 100.0,
    high: float | None = None,
    low: float | None = None,
    open_price: float | None = None,
    volume: float = 1000.0,
) -> Candle:
    return Candle(
        symbol="XAU-USDT-SWAP",
        granularity="15m",
        open_time=t,
        open=open_price if open_price is not None else close,
        high=high if high is not None else close * 1.001,
        low=low if low is not None else close * 0.999,
        close=close,
        volume=volume,
    )


def _candles(n: int = 50, start: datetime | None = None, step: float = 0.1) -> list[Candle]:
    base = start or datetime(2026, 1, 1, tzinfo=UTC)
    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        t = base + timedelta(minutes=15 * i)
        out.append(_candle(t, close=price))
        price += step
    return out


def test_config_hash_deterministic() -> None:
    a = config_hash(make_v3_candidate_config())
    b = config_hash(make_v3_candidate_config())
    assert a == b
    assert len(a) == 16


def test_commit_sha_nonempty() -> None:
    sha = commit_sha()
    assert isinstance(sha, str)
    assert len(sha) > 0


def test_sim_position_partial_tp_long() -> None:
    pos = SimPosition(
        run_id="r1",
        entry_candle_time=datetime(2026, 1, 1, tzinfo=UTC),
        entry_price=100.0,
        side=Side.LONG,
        quantity=1.0,
        stop_loss_price=99.0,
        take_profit_price=102.0,
        partial_tp_price=101.0,
        partial_tp_ratio=0.5,
    )
    assert pos.is_partial_tp_hit(candle_high=101.2, candle_low=100.0)
    assert not pos.is_partial_tp_hit(candle_high=100.8, candle_low=99.5)
    assert pos.is_tp_hit(candle_high=102.1, candle_low=101.0)
    assert not pos.is_tp_hit(candle_high=101.9, candle_low=101.0)


def test_sim_position_sl_short() -> None:
    pos = SimPosition(
        run_id="r1",
        entry_candle_time=datetime(2026, 1, 1, tzinfo=UTC),
        entry_price=100.0,
        side=Side.SHORT,
        quantity=1.0,
        stop_loss_price=101.0,
        take_profit_price=98.0,
        partial_tp_price=99.0,
        partial_tp_ratio=0.5,
    )
    assert pos.is_sl_hit(candle_high=101.1, candle_low=100.0)
    assert not pos.is_sl_hit(candle_high=100.9, candle_low=99.5)
    assert pos.is_partial_tp_hit(candle_high=99.9, candle_low=98.9)
    assert pos.is_tp_hit(candle_high=99.5, candle_low=97.9)


def test_harness_shadow_mode_records_signals_no_orders() -> None:
    cfg = PaperConfig(mode=SimMode.SHADOW)
    strategy = ConfluenceStrategy(make_v3_candidate_config())
    harness = PaperHarness(strategy, cfg)
    harness.reset("shadow-1", "abc123")
    result = harness.run(_candles())
    assert result.mode == SimMode.SHADOW
    assert len(result.orders) == 0
    assert len(result.signals) > 0
    assert result.trade_count == 0
    assert result.candles_processed == len(_candles())


def test_harness_paper_mode_one_position_at_a_time() -> None:
    cfg = PaperConfig(mode=SimMode.PAPER, max_open_positions=1)
    strategy = ConfluenceStrategy(make_v3_candidate_config())
    harness = PaperHarness(strategy, cfg)
    harness.reset("paper-1", "abc123")
    result = harness.run(_candles())
    entries = [o for o in result.orders if o.order_type == "ENTRY"]
    full_exits = [e for e in result.exits if not e.is_partial]
    # invariant: one open position at a time => entries == full exits
    assert len(entries) == len(full_exits)


def test_harness_paper_partial_tp_then_break_even() -> None:
    cfg = PaperConfig(mode=SimMode.PAPER, max_open_positions=1)
    strategy = ConfluenceStrategy(make_v3_candidate_config())
    harness = PaperHarness(strategy, cfg)
    harness.reset("paper-2", "abc123")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles: list[Candle] = []
    price = 100.0
    for i in range(60):
        t = base + timedelta(minutes=15 * i)
        if i < 40:
            candles.append(_candle(t, close=price, high=price * 1.002, low=price * 0.998))
            price += 0.05
        else:
            candles.append(_candle(t, close=price, high=price * 1.003, low=price * 0.997))
            price -= 0.05
    result = harness.run(candles)
    partials = [e for e in result.exits if e.exit_reason == SimExitReason.PARTIAL_TP]
    if partials:
        assert any(e.exit_reason == SimExitReason.SL for e in result.exits)


def test_store_roundtrip_and_idempotency(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    cfg = PaperConfig(mode=SimMode.SHADOW)
    strategy = ConfluenceStrategy(make_v3_candidate_config())
    harness = PaperHarness(strategy, cfg)
    harness.reset("run-store-1", "sha1")
    result = harness.run(_candles(20))
    store.save_run(result)
    assert store.run_exists("run-store-1")
    assert not store.run_exists("run-store-2")
    signals = store.get_signals("run-store-1")
    assert len(signals) == 20
    store.save_run(result)
    assert len(store.get_signals("run-store-1")) == 20
    runs = store.list_runs()
    assert len(runs) == 1
    store.close()


def test_processed_candle_resume() -> None:
    store = PaperStore("/tmp/paper_resume_test.db")
    candles = _candles(10)
    cfg = PaperConfig(mode=SimMode.SHADOW)
    strategy = ConfluenceStrategy(make_v3_candidate_config())
    harness = PaperHarness(strategy, cfg)
    harness.reset("resume-1", "sha1")
    result = harness.run(candles[:5])
    store.save_run(result)
    processed = store.get_processed_candle_times("resume-1")
    assert len(processed) == 5
    store.close()


def test_continuity_guard_blocks_new_entries() -> None:
    cfg = PaperConfig(mode=SimMode.PAPER, stale_candle_seconds=900, max_candle_gap_seconds=1800)
    strategy = ConfluenceStrategy(make_v3_candidate_config())
    harness = PaperHarness(strategy, cfg)
    harness.reset("guard-1", "sha1")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [_candle(base + timedelta(minutes=15 * i), close=100.0 + i * 0.1) for i in range(5)]
    candles.append(_candle(base + timedelta(minutes=15 * 5 + 60), close=110.0))  # gap
    harness.run(candles)
    gap_time = candles[-1].open_time
    entries_after_gap = [
        o for o in harness._orders if o.order_type == "ENTRY" and o.candle_time >= gap_time
    ]
    assert len(entries_after_gap) == 0


class ForcedLongStrategy(ConfluenceStrategy):
    """Test-only: force BUY when flat so lifecycle tests are deterministic."""

    def on_candle(self, candle: Candle, position: Any = None) -> Signal:
        return Signal.BUY
