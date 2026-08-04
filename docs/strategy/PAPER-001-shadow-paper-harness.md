# PROJECT-PAPER-001: Deterministic Shadow & Paper Trading Harness

**Status:** Research infrastructure. NOT production, NOT profitability claim.

The reference strategy `v3_candidate` has **no confirmed historical OOS edge**
(see PROJECT-BACKTEST-018: untouched aggregate −0.001R / PF 0.998; short side
−0.147R). This harness is for **execution-path correctness, reliability,
observability, and parity with the backtest engine** — not for claiming edge.

## Objective
Build an end-to-end deterministic shadow and simulated paper-trading harness
using the frozen `v3_candidate` profile, focused on execution correctness.

## Design
- `execution/paper/models.py` — data contracts: `SimSignal`, `SimOrder`,
  `SimPosition`, `SimExit`, `PaperRunResult`, `PaperConfig`, `SimMode`
- `execution/paper/harness.py` — candle-driven engine mirroring the backtest
  lifecycle (`SL -> partial TP -> TP -> signal`), ATR-based SL/TP, partial TP
  at 1R, break-even after partial, final TP/SL
- `execution/paper/store.py` — SQLite persistence + idempotent resume
- `execution/paper/runner.py` — `run_once` (deterministic single-pass) and
  `PaperRunner.run_loop` (OKX REST polling)
- `execution/paper/report.py` — daily JSON + markdown summaries
- `execution/paper/cli.py` — `xauusdt-paper shadow|paper [--once]`

## CLI
```bash
xauusdt-paper shadow --once                 # record signals, no positions
xauusdt-paper paper  --once                 # simulate full lifecycle (Postgres candles)
xauusdt-paper paper  --poll-interval 120    # poll OKX for new candles, persist, continue
```

`--once` runs over the stored candles from the frozen dataset selection
(`ds_xau_15m_365d_v1`). `--out-dir` controls where daily reports land
(default `docs/reports`).

## Determinism & safety
- Frozen `make_v3_candidate_config()` — no overrides
- No real orders, no private credentials, no LLM in decision path
- Deterministic slippage (`slippage_bps`) and fees (`fee_rate`) on every fill
- Idempotency: candle timestamps already processed are skipped on restart;
  `(run_id, candle_time)` unique — no duplicate signals/orders
- One open XAU-USDT-SWAP position at a time
- Stale-candle / candle-gap guards block new entries (existing positions honored)
- Every run records config hash, commit SHA, strategy label, run ID
- Replay parity tests compare paper vs backtest on same candle subset

## Verification
Replay parity (`tests/unit/test_paper_replay_parity.py`): the paper harness and
`ConfluenceBacktestEngine` agree on trade count, exit-reason distribution, and
final balance (within rounding) on the same candles/config — proving the harness
faithfully mirrors backtest execution.

## Guardrails
- Reports state the reference strategy has no confirmed historical OOS edge
- Paper results are clearly separated from backtest and real-trading results
- No strategy promotion, no parameter optimization, no production deployment