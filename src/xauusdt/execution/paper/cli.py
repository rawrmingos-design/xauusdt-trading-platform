"""CLI entry points for the shadow/paper harness (PROJECT-PAPER-001).

Commands:
  xauusdt-paper shadow --db PATH [--once]
  xauusdt-paper paper  --db PATH [--once]
  xauusdt-paper replay --db PATH --run RUN_ID   (replay latest results summary)

Shadow mode records signals + rejection reasons only.
Paper mode simulates the full position lifecycle with fees/slippage.
--once runs a single deterministic pass over stored candles from the
frozen dataset selection; without --once it polls OKX for new candles.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from xauusdt.exchange.models import Candle
from xauusdt.execution.paper.harness import DEFAULT_GRANULARITY, DEFAULT_SYMBOL, commit_sha
from xauusdt.execution.paper.models import PaperConfig, SimMode
from xauusdt.execution.paper.report import write_daily_report
from xauusdt.execution.paper.runner import PaperRunner, install_signal_handlers, run_once
from xauusdt.execution.paper.store import PaperStore
from xauusdt.strategy.confluence import ConfluenceStrategy, make_v3_candidate_config

log = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xauusdt-paper",
        description="Deterministic shadow/paper trading harness (PROJECT-PAPER-001)",
    )
    parser.add_argument(
        "mode",
        choices=["shadow", "paper"],
        help="shadow: record signals only; paper: simulate full lifecycle",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite path for paper state (default ~/.hermes/xauusdt_paper/paper_runs.db)",
    )
    parser.add_argument(
        "--once", action="store_true", help="single pass over stored candles, then exit"
    )
    parser.add_argument(
        "--poll-interval", type=int, default=120, help="seconds between OKX polls (default 120)"
    )
    parser.add_argument(
        "--max-cycles", type=int, default=None, help="stop after N poll cycles (testing)"
    )
    parser.add_argument("--out-dir", default="docs/reports", help="directory for daily reports")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument(
        "--run-id", default=None, help="override the run ID (default timestamp-based)"
    )
    return parser


def _strategy() -> ConfluenceStrategy:
    """Frozen v3_candidate reference strategy. No overrides."""
    return ConfluenceStrategy(make_v3_candidate_config())


async def _load_stored_candles(
    db_url: str, symbol: str = DEFAULT_SYMBOL, granularity: str = DEFAULT_GRANULARITY
) -> list[Candle]:
    """Load candles from the Postgres candle store (frozen dataset)."""
    from xauusdt.exchange.models import Candle
    from xauusdt.storage.candle_repository import CandleRepository
    from xauusdt.storage.database import get_session, init_db

    await init_db(db_url)
    orms: list[Any] | None = None
    async for session in get_session():
        repo = CandleRepository(session)
        orms = await repo.query_by_range(symbol, granularity, limit=500000)
        await session.close()
        break
    if not orms:
        return []
    candles = [
        Candle(
            symbol=symbol,
            granularity=granularity,
            open_time=r.open_time,
            open=float(r.open_price),
            high=float(r.high),
            low=float(r.low),
            close=float(r.close),
            volume=float(r.volume or 0),
            quote_volume=float(r.quote_volume or 0),
        )
        for r in orms
    ]
    candles.sort(key=lambda c: c.open_time)
    return candles


async def _async_main(args: argparse.Namespace) -> int:
    mode = SimMode.SHADOW if args.mode == "shadow" else SimMode.PAPER
    cfg = PaperConfig(mode=mode, max_open_positions=1)
    strategy = _strategy()
    store = PaperStore(args.db)
    commit = commit_sha()
    # Unique per execution so idempotent resume doesn't skip a fresh run.
    # --run-id overrides for deterministic replay / long-running polling.
    if args.run_id:
        run_id = args.run_id
    else:
        from datetime import UTC, datetime

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        run_id = f"{mode.value}_{commit[:8]}_{stamp}"

    if args.once:
        # deterministic single pass over stored candles (frozen dataset selection)
        from xauusdt.config import Settings

        candles = await _load_stored_candles(Settings().db_url)
        if not candles:
            print("No stored candles. Run PROJECT-DATA-010 backfill / dataset selection first.")
            return 1
        from xauusdt.execution.paper.harness import PaperHarness

        harness = PaperHarness(strategy, cfg)
        result = await run_once(harness, store, candles, run_id, commit, resume=True)
        path = write_daily_report(result, args.out_dir, prefix=f"paper_{mode.value}_once")
        print(
            f"Run {result.run_id}: {len(candles)} candles -> {result.trade_count} trades, PnL ${result.total_pnl:,.2f}"
        )
        print(f"Report: {path}")
        print(
            f"Signals: {len(result.signals)} | Executed: {sum(1 for s in result.signals if s.executed)} | Rejected: {sum(1 for s in result.signals if s.rejected)}"
        )
        return 0

    # polling mode: OKX REST collector
    runner = PaperRunner(
        strategy,
        store,
        run_id,
        commit,
        paper_cfg=cfg,
        poll_interval=args.poll_interval,
    )
    install_signal_handlers(runner)
    await runner.run_loop(max_cycles=args.max_cycles)
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        sys.exit(asyncio.run(_async_main(args)))
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
