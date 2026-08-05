"""PROJECT-FORWARD-OOS-001 evaluation package.

Pre-registered, frozen forward OOS evaluation for the continuous paper
runtime. Anti-peeking: strategy performance metrics are only computable
at/after the 2026-09-13 checkpoint; the status path exposes operational
data-quality only.
"""

from xauusdt.forward_oos.evaluator import (
    DataQualityError,
    MetricsReport,
    ParityReport,
    build_parity,
    checkpoint_end,
    compute_metrics,
    current_checkpoint,
    evaluate_gate,
    replay_offline,
    utc_now,
)
from xauusdt.forward_oos.manifest import (
    CHECKPOINT_60D,
    FORWARD_START,
    CandleAudit,
    audit_window,
)

__all__ = [
    "CHECKPOINT_60D",
    "FORWARD_START",
    "CandleAudit",
    "DataQualityError",
    "MetricsReport",
    "ParityReport",
    "audit_window",
    "build_parity",
    "checkpoint_end",
    "compute_metrics",
    "current_checkpoint",
    "evaluate_gate",
    "replay_offline",
    "utc_now",
]
