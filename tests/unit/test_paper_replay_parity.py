"""Replay parity tests: paper harness vs backtest engine on the same candles.

PROJECT-PAPER-001 acceptance: deterministic replay comparing paper execution
against backtest behavior on the same candle subset. The paper harness mirrors
the ConfluenceBacktestEngine semantics (SL -> partial TP -> TP -> signal), so
trade-level outcomes should be identical when both run the frozen
v3_candidate strategy with the same fees/slippage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig
from xauusdt.exchange.models import Candle
from xauusdt.execution.paper.harness import PaperHarness
from xauusdt.execution.paper.models import PaperConfig, SimMode
from xauusdt.strategy.confluence import ConfluenceStrategy, make_v3_candidate_config


def _candle(
    t: datetime,
    close: float,
    high: float | None = None,
    low: float | None = None,
    open_price: float | None = None,
) -> Candle:
    return Candle(
        symbol="XAU-USDT-SWAP",
        granularity="15m",
        open_time=t,
        open=open_price if open_price is not None else close,
        high=high if high is not None else close * 1.002,
        low=low if low is not None else close * 0.998,
        close=close,
        volume=1000.0,
    )


def _make_candles(n: int = 120) -> list[Candle]:
    """Synthetic alternating trend to produce entries and exits."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        t = base + timedelta(minutes=15 * i)
        phase = (i // 12) % 3  # 12 candles up, 12 down, 12 flat
        if phase == 0:
            price += 0.05
        elif phase == 1:
            price -= 0.05
        out.append(_candle(t, close=price))
    return out


def test_replay_parity_trade_outcomes() -> None:
    candles = _make_candles()
    cfg = make_v3_candidate_config()

    # backtest engine
    bt_cfg = BacktestConfig(
        initial_balance=10000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
        max_position_size_pct=1.0,
    )
    bt_engine = ConfluenceBacktestEngine(bt_cfg, candles, ConfluenceStrategy(cfg))
    bt_result = bt_engine.run()
    bt_full_exits = [t for t in bt_result.trades if not t.is_partial]

    # paper harness (same config, same candles)
    paper_cfg = PaperConfig(
        mode=SimMode.PAPER,
        initial_balance=10000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
        max_position_size_pct=1.0,
    )
    harness = PaperHarness(ConfluenceStrategy(cfg), paper_cfg)
    harness.reset("parity-1", "sha1")
    paper_result = harness.run(candles)
    paper_full_exits = [e for e in paper_result.exits if not e.is_partial]

    # both engines must agree on the number of completed trades
    assert len(paper_full_exits) == len(bt_full_exits), (
        f"trade count mismatch: paper={len(paper_full_exits)} backtest={len(bt_full_exits)}"
    )
    # and on final balance (same fees/slippage/exit behavior)
    assert abs(paper_result.final_balance - bt_result.metrics.final_balance) < 1.0, (
        f"balance mismatch: paper={paper_result.final_balance:.2f} "
        f"backtest={bt_result.metrics.final_balance:.2f}"
    )


def test_replay_parity_shadow_produces_no_trades() -> None:
    candles = _make_candles(60)
    cfg = make_v3_candidate_config()
    paper_cfg = PaperConfig(mode=SimMode.SHADOW)
    harness = PaperHarness(ConfluenceStrategy(cfg), paper_cfg)
    harness.reset("parity-shadow-1", "sha1")
    result = harness.run(candles)
    assert result.trade_count == 0
    assert len(result.orders) == 0
    assert len(result.signals) > 0


def test_replay_parity_exit_reason_distribution() -> None:
    candles = _make_candles()
    cfg = make_v3_candidate_config()

    bt_cfg = BacktestConfig(
        initial_balance=10000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
        max_position_size_pct=1.0,
    )
    bt_engine = ConfluenceBacktestEngine(bt_cfg, candles, ConfluenceStrategy(cfg))
    bt_result = bt_engine.run()
    bt_reasons = sorted(t.exit_reason for t in bt_result.trades)

    paper_cfg = PaperConfig(
        mode=SimMode.PAPER,
        initial_balance=10000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
        max_position_size_pct=1.0,
    )
    harness = PaperHarness(ConfluenceStrategy(cfg), paper_cfg)
    harness.reset("parity-2", "sha1")
    paper_result = harness.run(candles)
    paper_reasons = sorted(e.exit_reason.value for e in paper_result.exits)

    # PARTIAL_TP in backtest maps to "PARTIAL_TP"; paper uses same names
    assert paper_reasons == bt_reasons, f"reason mismatch: {paper_reasons} vs {bt_reasons}"
