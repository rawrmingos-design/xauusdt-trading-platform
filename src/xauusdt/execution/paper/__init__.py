"""PROJECT-PAPER-001: deterministic shadow & paper trading harness.

Subpackage layout:
  models.py  — data contracts (SimSignal, SimOrder, SimPosition, SimExit, PaperRunResult)
  harness.py — candle-driven deterministic execution engine (mirrors backtest)
  store.py   — SQLite persistence + idempotent resume
  runner.py  — polling loop + single-pass run_once
  report.py  — daily JSON/markdown summaries

Design constraints (pre-registered):
  - No real orders, no private credentials, no LLM in decision path.
  - v3_candidate stays frozen; harness is for execution-path correctness,
    reliability, observability, and parity with the backtest engine.
"""

from xauusdt.execution.paper.harness import PaperHarness, commit_sha, config_hash
from xauusdt.execution.paper.models import (
    PaperConfig,
    PaperRunResult,
    SimExit,
    SimExitReason,
    SimMode,
    SimOrder,
    SimPosition,
    SimSignal,
)
from xauusdt.execution.paper.report import build_daily_report, write_daily_report
from xauusdt.execution.paper.runner import PaperRunner, run_once
from xauusdt.execution.paper.store import PaperStore

__all__ = [
    "PaperHarness",
    "PaperConfig",
    "PaperRunResult",
    "SimExit",
    "SimExitReason",
    "SimMode",
    "SimOrder",
    "SimPosition",
    "SimSignal",
    "PaperStore",
    "PaperRunner",
    "run_once",
    "commit_sha",
    "config_hash",
    "build_daily_report",
    "write_daily_report",
]
