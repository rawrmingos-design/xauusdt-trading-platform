"""PROJECT-FORWARD-OOS-001 — deterministic tests for the frozen evaluator.

Synthetic fixtures only. Never touches real forward performance. Covers the
anti-peek gate (locked / 60d / 90d), dataset audit (gaps, dupes, coverage),
manifest reproducibility, paper-vs-replay parity, and entry-grouped metrics.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from xauusdt.backtest.models import BacktestTrade
from xauusdt.exchange.models import Candle
from xauusdt.execution.paper.store import PaperStore
from xauusdt.forward_oos.evaluator import (
    CHECKPOINT_60D,
    DataQualityError,
    build_parity,
    checkpoint_end,
    compute_metrics,
    current_checkpoint,
    evaluate_gate,
    replay_offline,
)
from xauusdt.forward_oos.manifest import FORWARD_START, audit_window


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
        high=high if high is not None else close * 1.002,
        low=low if low is not None else close * 0.998,
        close=close,
        volume=volume,
    )


def _forward_candles(n: int, start: datetime | None = None) -> list[Candle]:
    """Synthetic forward-window candles (never real performance)."""
    base = start or FORWARD_START
    return [
        _candle(base + timedelta(minutes=15 * i), close=100.0 + 0.1 * (i % 40)) for i in range(n)
    ]


def test_audit_complete_window() -> None:
    """Full window: 100% coverage, no gaps, no dupes, deterministic fingerprint."""
    end = FORWARD_START + timedelta(days=2)
    candles = _forward_candles(192, FORWARD_START)  # 2 days of 15m
    aud = audit_window("r1", candles, FORWARD_START, end)
    assert aud.complete
    assert aud.coverage_pct >= 99.5
    assert aud.gap_count == 0
    assert aud.duplicate_count == 0
    # reproducibility
    aud2 = audit_window("r1", candles, FORWARD_START, end)
    assert aud.fingerprint == aud2.fingerprint
    assert aud.fingerprint
    assert aud.manifest_json["expected_candles"] == 192


def test_audit_detects_gap() -> None:
    end = FORWARD_START + timedelta(days=1)
    candles = _forward_candles(96, FORWARD_START)
    # drop one middle candle to create a gap
    missing = candles[50]
    candles = [c for c in candles if c.open_time != missing.open_time]
    aud = audit_window("r2", candles, FORWARD_START, end)
    assert not aud.complete
    assert aud.gap_count == 1
    assert aud.coverage_pct < 99.5


def test_audit_detects_duplicate() -> None:
    end = FORWARD_START + timedelta(days=1)
    candles = _forward_candles(96, FORWARD_START)
    candles.append(candles[10])  # duplicate open_time
    aud = audit_window("r3", candles, FORWARD_START, end)
    assert not aud.complete
    assert aud.duplicate_count == 1


def test_gate_locked_before_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before 2026-09-13 the gate must refuse — and there is no bypass."""
    frozen = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("xauusdt.forward_oos.evaluator.utc_now", lambda: frozen)
    with pytest.raises(SystemExit) as ei:
        evaluate_gate()
    assert ei.value.code == 1


def test_gate_unlocks_at_60d(monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = CHECKPOINT_60D + timedelta(minutes=1)
    monkeypatch.setattr("xauusdt.forward_oos.evaluator.utc_now", lambda: frozen)
    lock = evaluate_gate()
    assert lock.unlocked
    assert current_checkpoint() == CHECKPOINT_60D


def test_checkpoint_end_bounds() -> None:
    assert checkpoint_end("60d") == CHECKPOINT_60D
    from xauusdt.forward_oos.manifest import CHECKPOINT_90D

    assert checkpoint_end("90d") == CHECKPOINT_90D
    with pytest.raises(ValueError):
        checkpoint_end("30d")


def test_manifest_reproducible_from_store(tmp_path: Path) -> None:
    """Stored candles → same audit twice → identical manifest."""
    db = tmp_path / "p.db"
    store = PaperStore(db)
    candles = _forward_candles(96, FORWARD_START)
    store.append_candles("run-x", candles)
    loaded = store.load_candles("run-x")
    end = FORWARD_START + timedelta(days=1)
    a1 = audit_window("run-x", loaded, FORWARD_START, end)
    a2 = audit_window("run-x", store.load_candles("run-x"), FORWARD_START, end)
    assert a1.fingerprint == a2.fingerprint
    assert a1.actual_count == 96
    assert store.candle_count("run-x") == 96
    store.close()


def test_store_append_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    store = PaperStore(db)
    candles = _forward_candles(10, FORWARD_START)
    store.append_candles("r", candles)
    store.append_candles("r", candles)  # same again
    assert store.candle_count("r") == 10  # dedup
    store.close()


def test_replay_offline_requires_candles() -> None:
    with pytest.raises(DataQualityError):
        replay_offline([])


def test_parity_report() -> None:
    p = build_parity(10, 10, 5, 5, 4, 4)
    assert p.signal_parity_pct == 100.0
    assert p.entry_parity_pct == 100.0
    p2 = build_parity(8, 10, 4, 5, 3, 4)
    assert p2.signal_parity_pct == 80.0


def _trade(
    entry: str,
    pnl: float,
    fee: float = 0.0,
    side: str = "LONG",
    reason: str = "TP",
    sl: float = 2.0,
    qty: float = 1.0,
    price: float = 100.0,
) -> BacktestTrade:
    return BacktestTrade(
        entry_candle_time=entry,
        entry_price=price,
        exit_candle_time=entry,
        exit_price=price + pnl / qty,
        side=side,
        quantity=qty,
        pnl=pnl,
        pnl_pct=0.0,
        fee=fee,
        exit_reason=reason,
        sl_distance=sl,
    )


def test_metrics_groups_partial_exits() -> None:
    """Multi-leg exits aggregate to one parent trade before R averaging."""
    tr = [
        _trade("2026-07-16T00:00:00+00:00", pnl=50.0, qty=1.0, price=100.0, sl=2.0),  # parent
        _trade("2026-07-16T00:00:00+00:00", pnl=50.0, qty=1.0, price=100.0, sl=2.0),  # partial leg
        _trade(
            "2026-07-17T00:00:00+00:00",
            pnl=-30.0,
            qty=1.0,
            price=100.0,
            sl=2.0,
            side="SHORT",
            reason="SL",
        ),
    ]

    class R:
        trades = tr
        max_drawdown_pct = 5.0
        metrics = None
        errors = []

    res = compute_metrics(R())
    # 2 parent trades, not 3
    assert res.trade_count == 2
    assert res.long_count == 1
    assert res.short_count == 1
    assert res.exit_reasons["TP"] == 1
    assert res.exit_reasons["SL"] == 1
    # parent1: 100/200=0.5R; parent2: -30/200=-0.15R
    assert abs(res.expectancy_r - (0.5 - 0.15) / 2) < 1e-6


def test_metrics_empty() -> None:
    class R:
        trades = []
        max_drawdown_pct = 0.0
        metrics = None
        errors = []

    res = compute_metrics(R())
    assert res.trade_count == 0
    assert res.expectancy_r == 0.0


def test_metrics_win_loss() -> None:
    tr = [
        _trade("2026-07-16T00:00:00+00:00", pnl=100.0, qty=1.0, price=100.0, sl=2.0),
        _trade("2026-07-17T00:00:00+00:00", pnl=-50.0, qty=1.0, price=100.0, sl=2.0),
    ]

    class R:
        trades = tr
        max_drawdown_pct = 3.0
        metrics = None
        errors = []

    res = compute_metrics(R())
    assert res.win_rate_pct == 50.0
    assert res.profit_factor == 2.0  # 100/50
    assert res.net_pnl == 50.0


# ---------------------------------------------------------------- backfill


def test_backfill_append_resume_and_idempotent(tmp_path: Path) -> None:
    """Backfill-like append: resume only missing, second run inserts zero."""
    db = tmp_path / "p.db"
    store = PaperStore(db)
    full = _forward_candles(96, FORWARD_START)
    # simulate runtime already persisted the first 10 candles
    store.append_candles("run-bf", full[:10])
    assert store.candle_count("run-bf") == 10

    # backfill pass 1: append the rest (idempotent per open_time)
    inserted = store.append_candles("run-bf", full)
    assert inserted == 86  # 96 - 10 already there
    assert store.candle_count("run-bf") == 96

    # backfill pass 2: identical input -> zero new rows
    inserted2 = store.append_candles("run-bf", full)
    assert inserted2 == 0
    assert store.candle_count("run-bf") == 96
    store.close()


def test_backfill_repairs_gap(tmp_path: Path) -> None:
    """A missing slot in the middle is filled by a later append."""
    db = tmp_path / "p.db"
    store = PaperStore(db)
    full = _forward_candles(96, FORWARD_START)
    # first pass stores all except one
    missing = full[50]
    partial = [c for c in full if c.open_time != missing.open_time]
    store.append_candles("run-g", partial)
    # second pass delivers the full set -> gap repaired, no dupes
    store.append_candles("run-g", full)
    loaded = store.load_candles("run-g")
    end = FORWARD_START + timedelta(days=1)
    aud = audit_window("run-g", loaded, FORWARD_START, end)
    assert aud.complete
    assert aud.gap_count == 0
    assert aud.duplicate_count == 0
    store.close()


def test_backfill_does_not_touch_execution_state(tmp_path: Path) -> None:
    """Backfill only writes paper_candles; signals/orders/exits untouched."""
    db = tmp_path / "p.db"
    store = PaperStore(db)
    candles = _forward_candles(10, FORWARD_START)
    store.append_candles("run-e", candles[:5])
    # simulate pre-existing execution records
    store.ensure_run("run-e", "paper", "v3", "hash", "commit", 10000.0)
    # backfill pass
    store.append_candles("run-e", candles)
    # execution tables still empty for this run
    assert len(store.get_signals("run-e")) == 0
    assert len(store.get_orders("run-e")) == 0
    assert len(store.get_exits("run-e")) == 0
    # only candle archive grew
    assert store.candle_count("run-e") == 10
    store.close()


def test_status_audits_to_now_not_future_checkpoint() -> None:
    """Coverage must be measured to utc_now; auditing to the 60d checkpoint
    would count every future 15m slot as a gap and false-alarm on data that
    simply hasn't been produced yet."""
    # A filled window of 4 days ending at "now" (no future slots expected yet).
    now = FORWARD_START + timedelta(days=4)
    candles = _forward_candles(4 * 96, FORWARD_START)  # first 4 full days
    # audit to "now": every grid slot to now is filled -> 100%
    a_now = audit_window("r", candles, FORWARD_START, now)
    assert a_now.coverage_pct == 100.0
    assert a_now.gap_count == 0
    # audit to the 60d checkpoint instead: all slots after `now` are future
    # and counted as missing -> coverage < 100%.
    a_checkpoint = audit_window("r", candles, FORWARD_START, CHECKPOINT_60D)
    assert a_checkpoint.coverage_pct < 100.0
    assert a_checkpoint.gap_count > a_now.gap_count
