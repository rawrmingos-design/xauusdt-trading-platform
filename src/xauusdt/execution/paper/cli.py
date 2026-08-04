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
        description="Deterministic shadow/paper trading harness (PROJECT-PAPER-001 / RISK-001)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # shadow / paper run modes (shared args)
    for mode in ("shadow", "paper"):
        p = sub.add_parser(mode, help=f"run {mode} mode")
        p.add_argument(
            "--db", default=None, help="SQLite path (default ~/.hermes/xauusdt_paper/paper_runs.db)"
        )
        p.add_argument(
            "--once", action="store_true", help="single pass over stored candles, then exit"
        )
        p.add_argument(
            "--poll-interval", type=int, default=120, help="seconds between OKX polls (default 120)"
        )
        p.add_argument(
            "--max-cycles", type=int, default=None, help="stop after N poll cycles (testing)"
        )
        p.add_argument("--out-dir", default="docs/reports", help="directory for daily reports")
        p.add_argument("--verbose", action="store_true", help="debug logging")
        p.add_argument(
            "--run-id", default=None, help="override the run ID (default timestamp-based)"
        )

    # risk control subcommand
    rp = sub.add_parser("risk", help="inspect/control risk state for a run")
    rp.add_argument(
        "--db", default=None, help="SQLite path (default ~/.hermes/xauusdt_paper/paper_runs.db)"
    )
    rp.add_argument("action", choices=["show", "kill-on", "kill-off", "reset"], help="risk action")
    rp.add_argument("--run-id", required=True, help="run_id whose risk state to inspect/mutate")
    rp.add_argument("--reason", default="", help="reason (required for kill-on/kill-off)")
    rp.add_argument("--verbose", action="store_true", help="debug logging")

    # monitoring subcommand (PROJECT-MONITORING-001)
    mp = sub.add_parser("monitor", help="runtime health monitoring & alerts")
    mp.add_argument(
        "--db", default=None, help="SQLite path (default ~/.hermes/xauusdt_paper/paper_runs.db)"
    )
    mp.add_argument(
        "action",
        choices=["health", "events", "alerts", "report", "test-telegram"],
        help="monitor action",
    )
    mp.add_argument("--run-id", required=True, help="run_id to inspect (health/events/report)")
    mp.add_argument("--limit", type=int, default=50, help="max events to list (events)")
    mp.add_argument("--out-dir", default="docs/reports", help="report output dir (report)")
    mp.add_argument("--verbose", action="store_true", help="debug logging")
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


def _risk_engine(db_path: str | None, run_id: str) -> Any:
    """Build a RiskEngine bound to a run, sharing the paper DB file."""
    from xauusdt.risk import RiskConfig, RiskEngine, RiskStore

    store = RiskStore(db_path, run_id=run_id)
    return RiskEngine(store, run_id, RiskConfig())


def _cmd_risk(args: argparse.Namespace) -> int:
    """Handle `xauusdt-paper risk ...` (inspect/mutate risk state)."""
    engine = _risk_engine(args.db, args.run_id)
    action = args.action
    if action == "show":
        snap = engine.snapshot(
            equity=_equity_for(args.db, args.run_id),
            open_positions=_open_positions_for(args.db, args.run_id),
        )
        print(snap.to_dict()["kill_switch"])
        print(
            f"equity={snap.equity:.2f} daily_loss={snap.daily_realized_loss:.2f}/{snap.daily_limit:.2f} "
            f"weekly_loss={snap.weekly_realized_loss:.2f}/{snap.weekly_limit:.2f} "
            f"consecutive_losses={snap.consecutive_losses} cooldown_active={snap.cooldown_active} "
            f"open_positions={snap.open_positions}/{snap.max_open_positions}"
        )
        if snap.last_rejection_codes:
            print(
                f"last_rejection={','.join(snap.last_rejection_codes)} "
                f"at {snap.last_rejection_time}"
            )
        return 0
    if action == "kill-on":
        if not args.reason:
            print("error: --reason required for kill-on")
            return 2
        ks = engine.enable_kill_switch(args.reason)
        print(f"kill switch ENABLED for run={args.run_id} reason={ks.reason}")
        return 0
    if action == "kill-off":
        if not args.reason:
            print("error: --reason required for kill-off")
            return 2
        ks = engine.disable_kill_switch(args.reason)
        print(f"kill switch DISABLED for run={args.run_id} reason={ks.reason}")
        return 0
    if action == "reset":
        engine.reset_counters()
        print(f"risk counters reset for run={args.run_id}")
        return 0
    return 1


def _equity_for(db_path: str | None, run_id: str) -> float:
    """Best-effort current equity from the paper run table."""
    store = PaperStore(db_path)
    run = store.get_run(run_id)
    return float(run["final_balance"]) if run else 10_000.0


def _open_positions_for(db_path: str | None, run_id: str) -> int:
    store = PaperStore(db_path)
    pos = store.get_position(run_id)
    return 1 if pos and pos.get("side") else 0


def _monitor_service(db_path: str | None) -> Any:
    """Build a MonitoringService bound to the same SQLite file."""
    from xauusdt.monitoring import MonitoringService, MonitorStore

    store = MonitorStore(db_path)
    return MonitoringService(store)


def _cmd_monitor(args: argparse.Namespace) -> int:
    """Handle `xauusdt-paper monitor ...` (health/events/alerts/report/test)."""
    from xauusdt.monitoring import write_daily_report

    svc = _monitor_service(args.db)
    action = args.action
    run_id = args.run_id
    if action == "health":
        h = svc.evaluate_health(run_id)
        print(
            f"run={run_id} alive={h['runtime_alive']} "
            f"heartbeat_age={h['heartbeat_age_seconds']}s "
            f"equity=${h['current_equity']:,.2f} "
            f"position={h['position_side'] or 'flat'} "
            f"issues={','.join(h['issues']) or 'none'}"
        )
        return 0
    if action == "events":
        for ev in svc._store.recent_events(run_id, limit=args.limit):
            print(
                f"{ev['timestamp']} {ev['severity']:8s} {ev['code']}"
                + (f" — {ev['message']}" if ev.get("message") else "")
            )
        return 0
    if action == "alerts":
        alerts = [a for a in svc._store.active_alerts() if a["run_id"] == run_id]
        if not alerts:
            print(f"run={run_id}: no active alerts")
            return 0
        for a in alerts:
            print(f"{a['code']} ({a['severity']}) first={a['first_seen']} last={a['last_seen']}")
        return 0
    if action == "report":
        jp, mp = write_daily_report(svc._store, run_id, args.out_dir, prefix="monitor")
        print(f"JSON: {jp}")
        print(f"Markdown: {mp}")
        return 0
    if action == "test-telegram":
        from xauusdt.monitoring import TelegramConfig, TelegramNotifier

        notifier = TelegramNotifier(svc._store, TelegramConfig())
        ok, detail = notifier.notify(run_id, "monitor_test", "INFO", "test message from CLI", {})
        print(f"delivered={ok} detail={detail}")
        return 0 if ok else 1
    return 1


async def _async_main(args: argparse.Namespace) -> int:
    command = args.command
    if command == "risk":
        return _cmd_risk(args)
    if command == "monitor":
        from xauusdt.monitoring import TelegramConfig

        # build with env-driven Telegram config (optional, disabled by default)
        msvc = _monitor_service(args.db)
        msvc.tg_cfg = TelegramConfig()
        return _cmd_monitor(args)

    mode = SimMode.SHADOW if command == "shadow" else SimMode.PAPER
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

    # attach risk engine in both shadow and paper modes
    risk_engine = _risk_engine(args.db, run_id)
    # observation-only monitoring service (Telegram optional)
    monitor = _monitor_service(args.db)

    if args.once:
        # deterministic single pass over stored candles (frozen dataset selection)
        from xauusdt.config import Settings

        candles = await _load_stored_candles(Settings().db_url)
        if not candles:
            print("No stored candles. Run PROJECT-DATA-010 backfill / dataset selection first.")
            return 1
        from xauusdt.execution.paper.harness import PaperHarness

        harness = PaperHarness(strategy, cfg, risk_engine=risk_engine, monitor=monitor)
        monitor.run_started(run_id, mode.value)
        result = await run_once(harness, store, candles, run_id, commit, resume=True)
        monitor.run_stopped(run_id, mode.value, "once-complete")
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
        risk_engine=risk_engine,
        monitor=monitor,
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
