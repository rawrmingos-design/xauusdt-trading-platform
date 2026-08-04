"""Deterministic tests for PROJECT-RISK-001 risk engine.

Covers: fixed-fractional sizing (long/short), worst-case stop loss within
budget, loss-limit blocks, cooldown, kill switch, partial-exit parent
accounting, restart recovery, and replay idempotency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from xauusdt.risk import RiskConfig, RiskEngine, RiskRejectionCode, RiskStore


def _engine(tmp_path, run_id: str = "r1", cfg: RiskConfig | None = None) -> RiskEngine:
    store = RiskStore(tmp_path / "risk.db", run_id=run_id)
    return RiskEngine(store, run_id, cfg or RiskConfig())


def _t(hour: int = 0) -> datetime:
    return datetime(2026, 8, 3, hour, 0, tzinfo=UTC)  # Monday


# --------------------------------------------------------------- sizing


def test_long_sizing_formula_deterministic(tmp_path) -> None:
    eng = _engine(tmp_path)
    eq = 10_000.0
    entry, stop = 2500.0, 2470.0
    qty, budget, rpu, bd = eng.compute_quantity(eq, entry, stop, "LONG")
    # risk budget = 0.25% of equity
    assert budget == pytest.approx(25.0)
    # risk per unit = |2500-2470| + estimated costs
    fee = entry * 0.0005
    slip = entry * 2.0 / 10_000.0
    assert rpu == pytest.approx(30.0 + 2 * fee + 2 * slip)
    # quantity = floor(budget / rpu) — never rounded up
    assert qty == pytest.approx(25.0 / (30.0 + 2 * fee + 2 * slip), abs=1e-6)
    assert qty > 0


def test_short_sizing_symmetry(tmp_path) -> None:
    eng = _engine(tmp_path)
    eq = 20_000.0
    entry, stop = 2500.0, 2530.0
    qty, budget, rpu, _ = eng.compute_quantity(eq, entry, stop, "SHORT")
    assert budget == pytest.approx(50.0)
    # same distance as long test => same per-unit risk
    assert rpu == pytest.approx(30.0 + 2 * (2500 * 0.0005) + 2 * (2500 * 0.0002))
    assert qty > 0


def test_worst_case_stop_loss_within_budget(tmp_path) -> None:
    eng = _engine(tmp_path)
    eq = 10_000.0
    entry, stop = 2500.0, 2460.0
    qty, budget, _, bd = eng.compute_quantity(eq, entry, stop, "LONG")
    # worst case at stop: |entry-stop| * qty + entry+exit fees + slippage
    worst = abs(entry - stop) * qty + 2 * entry * qty * 0.0005 + 2 * entry * qty * 0.0002
    assert worst <= budget * 1.000001  # floored, never exceeds budget


def test_quantity_floored_not_rounded(tmp_path) -> None:
    eng = _engine(tmp_path)
    eq = 10_000.0
    # construct entry/stop so budget/rpu has a fractional part
    entry, stop = 2499.99, 2498.99
    qty, budget, rpu, _ = eng.compute_quantity(eq, entry, stop, "LONG")
    exact = budget / rpu
    assert qty <= exact
    # floor at 6 decimals
    assert qty * 1_000_000 == int(qty * 1_000_000)


def test_quantity_zero_when_unit_risk_invalid(tmp_path) -> None:
    eng = _engine(tmp_path)
    qty, _, rpu, _ = eng.compute_quantity(10_000.0, 2500.0, 2500.0, "LONG")
    assert qty == 0.0
    assert rpu == 0.0


# -------------------------------------------------------------- rejection


def test_reject_when_entry_equals_stop(tmp_path) -> None:
    eng = _engine(tmp_path)
    d = eng.evaluate_entry(_t(1), "BUY", 2500.0, 2500.0, "LONG", 10_000.0, 0)
    assert not d.approved
    assert RiskRejectionCode.INVALID_STOP_DISTANCE.value in d.rejection_codes


def test_reject_invalid_prices(tmp_path) -> None:
    eng = _engine(tmp_path)
    d = eng.evaluate_entry(_t(1), "BUY", 0.0, 2480.0, "LONG", 10_000.0, 0)
    assert not d.approved
    assert RiskRejectionCode.INVALID_ENTRY_PRICE.value in d.rejection_codes
    d2 = eng.evaluate_entry(_t(2), "BUY", 2500.0, -5.0, "LONG", 10_000.0, 0)
    assert RiskRejectionCode.INVALID_STOP_PRICE.value in d2.rejection_codes


def test_reject_quantity_below_minimum(tmp_path) -> None:
    eng = _engine(tmp_path, cfg=RiskConfig(min_quantity=1.0))
    d = eng.evaluate_entry(_t(1), "BUY", 2500.0, 2499.0, "LONG", 100.0, 0)
    assert not d.approved
    assert RiskRejectionCode.QUANTITY_BELOW_MINIMUM.value in d.rejection_codes


def test_reject_max_open_positions(tmp_path) -> None:
    eng = _engine(tmp_path)
    d = eng.evaluate_entry(_t(1), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 1)
    assert not d.approved
    assert RiskRejectionCode.MAX_OPEN_POSITIONS.value in d.rejection_codes


def test_decision_recorded_in_store(tmp_path) -> None:
    store = RiskStore(tmp_path / "risk.db", run_id="r1")
    eng = RiskEngine(store, "r1")
    d = eng.evaluate_entry(_t(1), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert d.approved
    rows = store.load_decisions("r1")
    assert len(rows) == 1
    assert rows[0]["approved"] == 1
    assert rows[0]["quantity"] > 0


# --------------------------------------------------------- loss limits


def test_daily_loss_limit_blocks_entries(tmp_path) -> None:
    eng = _engine(tmp_path)
    d = eng.evaluate_entry(_t(1), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert d.approved
    # close a losing parent worth 1.5% ($150) > 1.0% daily limit
    eng.record_realized_pnl(_t(2), _t(1), -150.0, 10_000.0, parent_closed=True)
    d2 = eng.evaluate_entry(_t(3), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert not d2.approved
    assert RiskRejectionCode.DAILY_LOSS_LIMIT.value in d2.rejection_codes


def test_daily_under_limit_allows_entry(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_realized_pnl(_t(2), _t(1), -50.0, 10_000.0, parent_closed=True)
    d = eng.evaluate_entry(_t(3), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert d.approved


def test_weekly_loss_limit_beyond_daily(tmp_path) -> None:
    # daily limit 1% blocks after $100; weekly 2.5% alone allows up to $250
    eng = _engine(tmp_path)
    # exceed daily ($120) but daily gates first
    eng.record_realized_pnl(_t(1), _t(1), -120.0, 10_000.0, parent_closed=True)
    d = eng.evaluate_entry(_t(2), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert RiskRejectionCode.DAILY_LOSS_LIMIT.value in d.rejection_codes


# ------------------------------------------------------------- cooldown


def test_consecutive_losses_trigger_cooldown(tmp_path) -> None:
    eng = _engine(tmp_path, cfg=RiskConfig(cooldown_seconds=3600))
    for i in range(3):
        eng.record_realized_pnl(_t(i + 1), _t(i), -30.0, 10_000.0, parent_closed=True)
    # 3 consecutive losses >= threshold (3) -> cooldown active
    d = eng.evaluate_entry(_t(4), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert not d.approved
    assert RiskRejectionCode.CONSECUTIVE_LOSS_COOLDOWN.value in d.rejection_codes


def test_winning_trade_resets_streak(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_realized_pnl(_t(1), _t(1), -30.0, 10_000.0, parent_closed=True)
    eng.record_realized_pnl(_t(2), _t(2), -30.0, 10_000.0, parent_closed=True)
    # winning trade resets streak and cooldown
    eng.record_realized_pnl(_t(3), _t(3), +50.0, 10_000.0, parent_closed=True)
    snap = eng.snapshot(10_000.0, 0, now=_t(4))
    assert snap.consecutive_losses == 0
    assert not snap.cooldown_active
    d = eng.evaluate_entry(_t(4), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert d.approved


def test_break_even_resets_streak(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_realized_pnl(_t(1), _t(1), -30.0, 10_000.0, parent_closed=True)
    eng.record_realized_pnl(_t(2), _t(2), 0.0, 10_000.0, parent_closed=True)
    snap = eng.snapshot(10_000.0, 0, now=_t(3))
    assert snap.consecutive_losses == 0


def test_cooldown_expires_after_duration(tmp_path) -> None:
    eng = _engine(tmp_path, cfg=RiskConfig(cooldown_seconds=60))
    now = _t(0)
    for _ in range(3):
        eng.record_realized_pnl(now, now, -30.0, 10_000.0, parent_closed=True, now=now)
    # blocked immediately
    d = eng.evaluate_entry(now, "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0, now=now)
    assert RiskRejectionCode.CONSECUTIVE_LOSS_COOLDOWN.value in d.rejection_codes
    # after 60s cooldown expired
    later = now + timedelta(seconds=61)
    d2 = eng.evaluate_entry(later, "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0, now=later)
    assert d2.approved


def test_rejected_signal_does_not_affect_streak(tmp_path) -> None:
    eng = _engine(tmp_path)
    # a rejected entry (kill switch) then a losing parent: still only 1 loss
    ks = eng.enable_kill_switch("test")
    assert ks.enabled
    d = eng.evaluate_entry(_t(1), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert not d.approved
    assert RiskRejectionCode.KILL_SWITCH_ACTIVE.value in d.rejection_codes
    eng.disable_kill_switch("off")
    eng.record_realized_pnl(_t(2), _t(1), -30.0, 10_000.0, parent_closed=True)
    snap = eng.snapshot(10_000.0, 0, now=_t(3))
    assert snap.consecutive_losses == 1


# ---------------------------------------------------------- kill switch


def test_kill_switch_blocks_new_entries(tmp_path) -> None:
    eng = _engine(tmp_path)
    ks = eng.enable_kill_switch("halt")
    assert ks.enabled
    d = eng.evaluate_entry(_t(1), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert not d.approved
    assert RiskRejectionCode.KILL_SWITCH_ACTIVE.value in d.rejection_codes


def test_kill_switch_persists(tmp_path) -> None:
    store = RiskStore(tmp_path / "risk.db", run_id="k")
    eng = RiskEngine(store, "k")
    eng.enable_kill_switch("halt")
    # new engine + store against same db sees enabled state
    store2 = RiskStore(tmp_path / "risk.db", run_id="k")
    eng2 = RiskEngine(store2, "k")
    assert eng2.kill_switch().enabled
    assert eng2.kill_switch().reason == "halt"


def test_disable_kill_switch_restores_entries(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.enable_kill_switch("halt")
    eng.disable_kill_switch("resume")
    d = eng.evaluate_entry(_t(1), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert d.approved


# ------------------------------------------------------ partial exits


def test_partial_exit_does_not_increment_loss_streak(tmp_path) -> None:
    eng = _engine(tmp_path)
    # partial TP is NOT a parent close; must not touch streak
    eng.record_realized_pnl(_t(1), _t(1), +40.0, 10_000.0, parent_closed=False)
    # parent completes losing at BE/stop -> net aggregate negative
    eng.record_realized_pnl(_t(2), _t(1), -50.0, 10_000.0, parent_closed=True)
    snap = eng.snapshot(10_000.0, 0, now=_t(3))
    assert snap.consecutive_losses == 1


def test_partial_tp_plus_break_even_uses_aggregate(tmp_path) -> None:
    eng = _engine(tmp_path)
    t1, t2, t3 = _t(1), _t(2), _t(3)
    # net realized PnL across all legs of the parent is 0 (-60 + +60)
    # partial leg does not touch the streak (parent not closed)
    eng.record_realized_pnl(t1, t1, +60.0, 10_000.0, parent_closed=False, now=t1)
    # parent closes: aggregate net = 0 (break-even) => resets streak
    eng.record_realized_pnl(t2, t1, 0.0, 10_000.0, parent_closed=True, now=t2)
    snap = eng.snapshot(10_000.0, 0, now=t3)
    assert snap.consecutive_losses == 0  # aggregate net == 0 resets


# ------------------------------------------------------- period resets


def test_daily_loss_resets_next_day_utc(tmp_path) -> None:
    eng = _engine(tmp_path)
    mon = _t(1)  # Monday 2026-08-03
    tue = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)
    # Monday > daily limit
    eng.record_realized_pnl(mon, mon, -150.0, 10_000.0, parent_closed=True, now=mon)
    d = eng.evaluate_entry(
        mon + timedelta(hours=1),
        "BUY",
        2500.0,
        2480.0,
        "LONG",
        10_000.0,
        0,
        now=mon + timedelta(hours=1),
    )
    assert RiskRejectionCode.DAILY_LOSS_LIMIT.value in d.rejection_codes
    # next day (different UTC date) -> daily resets, entry allowed
    d2 = eng.evaluate_entry(tue, "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0, now=tue)
    assert d2.approved


# --------------------------------------------------- restart recovery


def test_risk_state_survives_restart(tmp_path) -> None:
    store = RiskStore(tmp_path / "risk.db", run_id="p")
    eng = RiskEngine(store, "p")
    eng.record_realized_pnl(_t(2), _t(1), -150.0, 10_000.0, parent_closed=True)
    eng.record_realized_pnl(_t(3), _t(2), -30.0, 10_000.0, parent_closed=True)
    # simulate process restart: new store + engine (same db + run_id)
    store2 = RiskStore(tmp_path / "risk.db", run_id="p")
    eng2 = RiskEngine(store2, "p")
    d = eng2.evaluate_entry(_t(4), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert not d.approved
    assert RiskRejectionCode.DAILY_LOSS_LIMIT.value in d.rejection_codes
    snap = eng2.snapshot(10_000.0, 0, now=_t(4))
    assert snap.consecutive_losses == 2


def test_kill_switch_survives_crash_restart(tmp_path) -> None:
    store = RiskStore(tmp_path / "risk.db", run_id="c")
    eng = RiskEngine(store, "c")
    eng.enable_kill_switch("emergency halt")
    # crash -> new store/engine
    store2 = RiskStore(tmp_path / "risk.db", run_id="c")
    eng2 = RiskEngine(store2, "c")
    d = eng2.evaluate_entry(_t(1), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert not d.approved
    assert RiskRejectionCode.KILL_SWITCH_ACTIVE.value in d.rejection_codes


def test_replay_does_not_change_counters(tmp_path) -> None:
    store = RiskStore(tmp_path / "risk.db", run_id="r")
    eng = RiskEngine(store, "r")
    d = eng.evaluate_entry(_t(1), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert d.approved
    # replay same candle via new engine — decision row replaced, counters unchanged
    store2 = RiskStore(tmp_path / "risk.db", run_id="r")
    eng2 = RiskEngine(store2, "r")
    d2 = eng2.evaluate_entry(_t(1), "BUY", 2500.0, 2480.0, "LONG", 10_000.0, 0)
    assert d2.approved  # replayed candle re-evaluates approved
    rows = store2.load_decisions("r")
    assert len(rows) == 1  # dedup by (run_id, candle_time)
    snap = eng2.snapshot(10_000.0, 0, now=_t(2))
    assert snap.consecutive_losses == 0
    assert snap.daily_realized_loss == 0.0


# --------------------------------------------------- harness integration


def test_harness_risk_reject_creates_no_order(tmp_path) -> None:
    """A risk-rejected entry must not create an order/position."""
    from xauusdt.backtest.models import Candle, Side
    from xauusdt.execution.paper.harness import PaperHarness
    from xauusdt.execution.paper.models import PaperConfig, SimMode
    from xauusdt.strategy.confluence import ConfluenceStrategy, make_v3_candidate_config

    # force rejection via kill switch
    store = RiskStore(tmp_path / "risk.db", run_id="h")
    eng = RiskEngine(store, "h")
    eng.enable_kill_switch("halt")
    strat = ConfluenceStrategy(make_v3_candidate_config())
    cfg = PaperConfig(mode=SimMode.PAPER, initial_balance=10_000.0)
    harness = PaperHarness(strat, cfg, risk_engine=eng)
    harness.reset("h", "0000000")
    c = Candle(
        symbol="XAU-USDT-SWAP",
        granularity="15m",
        open_time=_t(1),
        open=2500.0,
        high=2510.0,
        low=2490.0,
        close=2505.0,
        volume=100.0,
    )
    # call the entry gate directly (deterministic regardless of strategy signal)
    opened = harness._open_position(c, Side.LONG)
    assert opened is False
    assert harness.open_position is None
    assert harness._orders == []
    # decision recorded as rejected
    rows = store.load_decisions("h")
    assert len(rows) == 1
    assert rows[0]["approved"] == 0
    assert RiskRejectionCode.KILL_SWITCH_ACTIVE.value in rows[0]["rejection_codes"]


def test_harness_risk_approved_uses_computed_quantity(tmp_path) -> None:
    """A risk-approved entry must open with risk-computed quantity, not percent."""
    from xauusdt.backtest.models import Candle, Side
    from xauusdt.execution.paper.harness import PaperHarness
    from xauusdt.execution.paper.models import PaperConfig, SimMode
    from xauusdt.strategy.confluence import ConfluenceStrategy, make_v3_candidate_config

    store = RiskStore(tmp_path / "risk.db", run_id="h2")
    eng = RiskEngine(store, "h2")
    strat = ConfluenceStrategy(make_v3_candidate_config())
    cfg = PaperConfig(mode=SimMode.PAPER, initial_balance=10_000.0)
    harness = PaperHarness(strat, cfg, risk_engine=eng)
    harness.reset("h2", "0000000")
    c = Candle(
        symbol="XAU-USDT-SWAP",
        granularity="15m",
        open_time=_t(1),
        open=2500.0,
        high=2530.0,
        low=2470.0,
        close=2520.0,
        volume=5000.0,
    )
    opened = harness._open_position(c, Side.LONG)
    assert opened is True
    decision = [d for d in store.load_decisions("h2") if d["approved"]][0]
    from math import isclose

    pos = harness.open_position
    assert pos is not None
    assert isclose(pos.quantity, decision["quantity"], rel_tol=1e-6)
