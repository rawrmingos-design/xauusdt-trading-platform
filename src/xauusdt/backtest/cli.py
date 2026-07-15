"""CLI entry point for backtest runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xauusdt.backtest.engine import BacktestEngine
from xauusdt.backtest.models import BacktestConfig
from xauusdt.backtest.strategies import (
    AlwaysHold,
    BaseStrategy,
    SimpleMACrossover,
    SimpleSLTPStrategy,
)
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import init_db

DEFAULT_SYMBOL = "XAU-USDT-SWAP"
DB_URL = "sqlite+aiosqlite:///xauusdt.db"

GRANULARITIES = {"5m", "15m", "1H", "4H"}


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid datetime {value!r}. Use ISO-8601 format "
            f"(e.g. 2025-01-01T00:00:00Z or 2025-01-01T00:00:00+00:00)"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xauusdt-backtest",
        description="Backtest XAUUSDT strategies on stored OKX candles",
    )
    parser.add_argument(
        "--symbol",
        default=DEFAULT_SYMBOL,
        help="Symbol (default: XAU-USDT-SWAP)",
    )
    parser.add_argument(
        "--granularity",
        default="5m",
        choices=sorted(GRANULARITIES),
        help="Candle granularity (default: 5m)",
    )
    parser.add_argument(
        "--start-time",
        type=_parse_datetime,
        required=True,
        help="Start datetime (ISO-8601)",
    )
    parser.add_argument(
        "--end-time",
        type=_parse_datetime,
        required=True,
        help="End datetime (ISO-8601)",
    )
    parser.add_argument(
        "--strategy",
        default="always_hold",
        choices=["always_hold", "ma_crossover", "simple_sltP"],
        help="Strategy to use (default: always_hold)",
    )
    parser.add_argument(
        "--strategy-args",
        default="",
        help="Comma-separated key=value strategy params (e.g. short_window=5,long_window=20)",
    )
    parser.add_argument(
        "--initial-balance",
        type=float,
        default=10000.0,
        help="Initial balance (default: 10000)",
    )
    parser.add_argument(
        "--fee-rate",
        type=float,
        default=0.0006,
        help="Fee rate per trade (default: 0.0006 = 0.06%%)",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=5.0,
        help="Slippage in basis points (default: 5 = 0.05%%)",
    )
    parser.add_argument(
        "--stop-loss-pct",
        type=float,
        default=0.0,
        help="Stop-loss percentage (0 = disabled, e.g. 0.01 = 1%%)",
    )
    parser.add_argument(
        "--take-profit-pct",
        type=float,
        default=0.0,
        help="Take-profit percentage (0 = disabled, e.g. 0.02 = 2%%)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backtest_result.json"),
        help="Output file path (default: backtest_result.json)",
    )
    parser.add_argument(
        "--db-url",
        default=DB_URL,
        help="Database URL (default: sqlite+aiosqlite:///xauusdt.db)",
    )
    return parser


def _build_strategy(strategy_name: str, strategy_args: str) -> BaseStrategy:
    args: dict[str, Any] = {}
    if strategy_args:
        for kv in strategy_args.split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    args[k] = int(v)
                except ValueError:
                    try:
                        args[k] = float(v)
                    except ValueError:
                        args[k] = v

    match strategy_name:
        case "always_hold":
            return AlwaysHold()
        case "ma_crossover":
            return SimpleMACrossover(
                short_window=args.get("short_window", 5),
                long_window=args.get("long_window", 20),
            )
        case "simple_sltP":
            return SimpleSLTPStrategy(candle_interval=args.get("interval", 5))
        case _:
            raise ValueError(f"Unknown strategy: {strategy_name}")


async def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config = BacktestConfig(
        initial_balance=args.initial_balance,
        fee_rate=args.fee_rate,
        slippage_bps=args.slippage_bps,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
    )

    _build_strategy(args.strategy, args.strategy_args)

    # Load candles from DB
    await init_db(args.db_url)
    from xauusdt.storage.database import session_factory as _sf

    _session_factory = _sf
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db first.")
    _session = _session_factory()
    repo = CandleRepository(_session)
    candles_orm = await repo.query_by_range(
        symbol=args.symbol,
        granularity=args.granularity,
        start_time=args.start_time,
        end_time=args.end_time,
    )
    await _session.close()

    if not candles_orm:
        print(
            f"No candles found for {args.symbol} {args.granularity} "
            f"{args.start_time} → {args.end_time}"
        )
        sys.exit(1)

    # Convert CandleOrm to Candle
    candle_objs: list[Candle] = [
        Candle(
            open_time=c.open_time,  # type: ignore[arg-type]
            symbol=args.symbol,
            granularity=args.granularity,
            open=float(c.open_price),
            high=float(c.high),
            low=float(c.low),
            close=float(c.close),
            volume=float(c.volume or 0),
        )
        for c in candles_orm
    ]

    # Run backtest
    engine = BacktestEngine(config, candle_objs)
    result = engine.run()

    # Output
    output = result.to_dict()
    output_file = args.output

    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{'=' * 60}")
    print("BACKTEST RESULTS")
    print(f"{'=' * 60}")
    print(f"Strategy:         {args.strategy}")
    print(f"Symbol:           {args.symbol}")
    print(f"Granularity:      {args.granularity}")
    print(f"Candles:          {len(candle_objs)}")
    print(f"Period:           {args.start_time} → {args.end_time}")
    print(f"Initial Balance:  {config.initial_balance:,.2f}")
    print(f"Final Balance:    {result.metrics.final_balance:,.2f}")
    print(f"Net PnL:          {result.metrics.net_pnl:,.2f}")
    print(f"Total Trades:     {result.metrics.total_trades}")
    print(f"Win Rate:         {result.metrics.win_rate * 100:.1f}%%")
    print(f"Profit Factor:    {result.metrics.profit_factor:.4f}")
    print(
        f"Max Drawdown:     {result.metrics.max_drawdown:.2f} ({result.metrics.max_drawdown_pct * 100:.2f}%%)"
    )
    print(f"Expectancy:       {result.metrics.expectancy:,.2f}")
    print(f"{'=' * 60}")
    print(f"Output: {output_file}")

    if output_file != Path("backtest_result.json"):
        print(f"Markdown: {output_file.with_suffix('.md')}")
        md = output_file.with_suffix(".md")
        with open(md, "w") as f:
            f.write("# Backtest Report\n\n")
            f.write(f"Strategy: {args.strategy}\n")
            f.write(f"Symbol: {args.symbol}\n")
            f.write(f"Granularity: {args.granularity}\n")
            f.write(f"Period: {args.start_time} to {args.end_time}\n\n")
            f.write("## Metrics\n\n")
            f.write("| Metric | Value |\n")
            f.write("|---|---|\n")
            for k, v in result.metrics.to_dict().items():
                if k != "trade_list":
                    f.write(f"| {k} | {v} |\n")
            f.write("\n## Trades\n\n")
            f.write("| # | Side | Entry | Exit | PnL | Reason |\n")
            f.write("|---|------|-------|------|-----|--------|\n")
            for i, t in enumerate(result.trades, 1):
                f.write(
                    f"| {i} | {t.side} | {t.entry_price:.2f} | {t.exit_price:.2f} | {t.pnl:.2f} | {t.exit_reason} |\n"
                )


if __name__ == "__main__":
    asyncio.run(main())
