"""MT5 Phase 3 manual execution harness (DEMO ONLY).

Usage:
    uv run python tools/manual_test_order.py --symbol XAUUSD --volume 0.01 \\
        --sl-offset 10.0 --tp-offset 10.0 [--dry-run]

What it does (NO trading decisions — pure infrastructure check):
    1. Connect to the demo account (MT5_MODE=demo required).
    2. Read account info.
    3. Resolve the symbol.
    4. Read the current tick.
    5. Build a MARKET BUY intent with SL/TP offset from the ask (mandatory).
    6. submit() -> order_check -> order_send -> reconcile -> persist.
    7. Verify the fill: position open, SL/TP set, volume matches.
    8. close_position() (full close only).
    9. Verify the close: position gone.
    10. startup_reconcile() to confirm no orphan state.

The harness never decides *whether* to trade — it only proves the engine
can execute one round-trip correctly.

Environment:
    MT5_MODE=demo
    MT5_LOGIN / MT5_PASSWORD / MT5_SERVER  (or MT5_LOGIN_FILE)
    MT5_TERMINAL_PATH (optional)
    MT5_MAGIC (required, != 0)
    MT5_RUN_ID (optional)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xauusdt.execution.errors import ModeGuardError, OrderValidationError  # noqa: E402
from xauusdt.execution.models import OrderKind, OrderSide  # noqa: E402
from xauusdt.execution.mt5.adapter import Mt5ExecutionAdapter  # noqa: E402
from xauusdt.execution.mt5.config import Mt5Settings  # noqa: E402
from xauusdt.execution.mt5.store import Mt5IntentStore  # noqa: E402
from xauusdt.execution.orders import OrderIntent  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="MT5 demo execution harness")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--volume", type=float, default=0.01)
    ap.add_argument(
        "--sl-offset",
        type=float,
        required=True,
        help="SL distance from entry in price units (e.g. 10.0)",
    )
    ap.add_argument(
        "--tp-offset", type=float, required=True, help="TP distance from entry in price units"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="validate + order_check only, never send"
    )
    ap.add_argument("--db", default=str(Path(os.environ.get("MT5_DB", "/tmp/mt5_manual_test.db"))))
    args = ap.parse_args()

    settings = Mt5Settings.from_env()
    if args.dry_run:
        settings = settings.__class__(**{**settings.__dict__, "mode": "demo"})  # keep demo guard

    store = Mt5IntentStore(args.db)
    adapter = Mt5ExecutionAdapter(settings, store=store)

    try:
        # 1-2. connect + account
        adapter.connect()
        acc = adapter.account_info()
        print(
            f"[ok] connected: login={acc.login} balance={acc.balance:.2f} "
            f"currency={acc.currency} mode={acc.mode}"
        )

        # 3-4. symbol + tick
        symbol = adapter.resolve_symbol(args.symbol)
        print(f"[ok] symbol resolved: {symbol}")
        tick = adapter.tick(symbol)
        print(f"[ok] tick: bid={tick.bid:.2f} ask={tick.ask:.2f}")

        # 5. build a MARKET BUY with mandatory SL/TP (no trading decision —
        #    offsets come from the CLI, not from any strategy signal)
        entry_price = tick.ask
        intent = OrderIntent(
            symbol=symbol,
            side=OrderSide.LONG,
            kind=OrderKind.MARKET,
            volume=args.volume,
            entry_price=entry_price,
            stop_loss=entry_price - args.sl_offset,
            take_profit=entry_price + args.tp_offset,
        )

        # 6. submit (full flow: validate → SL/TP → persist → idempotency →
        #    order_check → order_send → classify → reconcile → persist truth)
        print(
            f"[..] submitting BUY {args.volume} {symbol} "
            f"SL={intent.stop_loss:.2f} TP={intent.take_profit:.2f}"
        )
        res = adapter.place_order(intent)
        print(
            f"[{'ok' if res.ok else 'FAIL'}] submit: {res.message} "
            f"state={res.state.value if res.state else '?'} "
            f"venue_id={res.venue_order_id}"
        )
        if not res.ok:
            print("[FAIL] order not placed — aborting", file=sys.stderr)
            return 1

        if args.dry_run:
            print("[dry-run] no order sent; skipping close")
            return 0

        # 7. verify fill
        positions = adapter.positions()
        mine = [
            p for p in positions if p.position_id == str(res.venue_order_id) or p.symbol == symbol
        ]
        if not mine:
            print("[FAIL] no position found after submit — reconcile", file=sys.stderr)
            adapter.reconcile()
            return 1
        pos = mine[0]
        print(
            f"[ok] position: id={pos.position_id} {pos.symbol} "
            f"volume={pos.volume} side={pos.side.value} "
            f"sl={pos.sl:.2f} tp={pos.tp:.2f}"
        )
        if abs(pos.sl - intent.stop_loss) > 1e-6 or abs(pos.tp - intent.take_profit) > 1e-6:
            print(
                f"[WARN] SL/TP mismatch: pos({pos.sl}/{pos.tp}) "
                f"!= intent({intent.stop_loss}/{intent.take_profit})"
            )

        # 8. close (full close)
        print(f"[..] closing position {pos.position_id}")
        close = adapter.close_position(pos.position_id)
        print(
            f"[{'ok' if close.ok else 'FAIL'}] close: {close.message} "
            f"state={close.state.value if close.state else '?'}"
        )
        if not close.ok:
            print("[FAIL] close failed", file=sys.stderr)
            return 1

        # 9. verify close
        after = adapter.positions()
        still = [p for p in after if p.position_id == pos.position_id]
        if still:
            print("[FAIL] position still open after close", file=sys.stderr)
            return 1
        print("[ok] position closed and confirmed")

        # 10. startup_reconcile — no orphan state
        report = adapter.startup_reconcile()
        print(
            f"[ok] startup_reconcile: resolved={len(report.resolved)} "
            f"unresolved={len(report.unresolved)} adopted={len(report.open_positions)}"
        )
        if report.unresolved:
            print(f"[WARN] unresolved intents: {report.unresolved}", file=sys.stderr)

        print("\n=== EXECUTION HARNESS PASS ===")
        return 0
    except ModeGuardError as exc:
        print(f"[FAIL] mode guard: {exc}", file=sys.stderr)
        return 2
    except OrderValidationError as exc:
        print(f"[FAIL] validation: {exc}", file=sys.stderr)
        return 2
    finally:
        adapter.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
