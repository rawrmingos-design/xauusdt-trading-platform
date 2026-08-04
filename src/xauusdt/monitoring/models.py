"""Monitoring models for PROJECT-MONITORING-001.

Observation-only runtime health monitoring for the shadow/paper harness.
Defines event severities, event codes, heartbeat state, and alert state.
All timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Event codes (the full alert surface). Every runtime anomaly maps to one of
# these so alerts are machine-stable (compare against strings, not UI labels).
# ---------------------------------------------------------------------------
class EventCode(StrEnum):
    RUNTIME_HEARTBEAT_MISSING = "monitor_runtime_heartbeat_missing"
    COLLECTOR_ERROR = "monitor_collector_error"
    COLLECTOR_RECOVERED = "monitor_collector_recovered"
    STALE_CANDLE = "monitor_stale_candle"
    CANDLE_GAP = "monitor_candle_gap"
    DUPLICATE_ATTEMPT = "monitor_duplicate_attempt"
    DATABASE_WRITE_FAILED = "monitor_database_write_failed"
    STATE_RECOVERY_FAILED = "monitor_state_recovery_failed"
    POSITION_RECOVERY_FAILED = "monitor_position_recovery_failed"
    DAILY_LOSS_LIMIT_REACHED = "monitor_daily_loss_limit_reached"
    WEEKLY_LOSS_LIMIT_REACHED = "monitor_weekly_loss_limit_reached"
    CONSECUTIVE_LOSS_COOLDOWN = "monitor_consecutive_loss_cooldown"
    KILL_SWITCH_ENABLED = "monitor_kill_switch_enabled"
    KILL_SWITCH_DISABLED = "monitor_kill_switch_disabled"
    RUN_STARTED = "monitor_run_started"
    RUN_STOPPED = "monitor_run_stopped"


# Map each event code to its default severity (overridable on record).
EVENT_SEVERITY: dict[EventCode, Severity] = {
    EventCode.RUN_STARTED: Severity.INFO,
    EventCode.RUN_STOPPED: Severity.INFO,
    EventCode.COLLECTOR_RECOVERED: Severity.INFO,
    EventCode.KILL_SWITCH_DISABLED: Severity.INFO,
    EventCode.COLLECTOR_ERROR: Severity.WARNING,
    EventCode.STALE_CANDLE: Severity.WARNING,
    EventCode.CANDLE_GAP: Severity.WARNING,
    EventCode.CONSECUTIVE_LOSS_COOLDOWN: Severity.WARNING,
    EventCode.DUPLICATE_ATTEMPT: Severity.WARNING,
    EventCode.RUNTIME_HEARTBEAT_MISSING: Severity.CRITICAL,
    EventCode.DATABASE_WRITE_FAILED: Severity.CRITICAL,
    EventCode.STATE_RECOVERY_FAILED: Severity.CRITICAL,
    EventCode.POSITION_RECOVERY_FAILED: Severity.CRITICAL,
    EventCode.DAILY_LOSS_LIMIT_REACHED: Severity.CRITICAL,
    EventCode.WEEKLY_LOSS_LIMIT_REACHED: Severity.CRITICAL,
    EventCode.KILL_SWITCH_ENABLED: Severity.CRITICAL,
}


class MonitoringEvent:
    """A structured operational event with a stable code and severity."""

    __slots__ = (
        "run_id",
        "code",
        "severity",
        "message",
        "timestamp",
        "metadata",
    )

    def __init__(
        self,
        run_id: str,
        code: EventCode | str,
        severity: Severity | str | None = None,
        message: str = "",
        timestamp: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from datetime import UTC

        self.run_id = run_id
        self.code = code.value if isinstance(code, EventCode) else str(code)
        if severity is None:
            try:
                severity = EVENT_SEVERITY[EventCode(self.code)]
            except (ValueError, KeyError):
                severity = Severity.INFO
        self.severity = severity.value if isinstance(severity, Severity) else str(severity)
        self.message = message
        self.timestamp = timestamp or datetime.now(UTC)
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class Heartbeat:
    """Current runtime heartbeat for a single run_id (persisted per cycle)."""

    __slots__ = (
        "run_id",
        "mode",
        "strategy_label",
        "config_hash",
        "commit_sha",
        "db_path",
        "last_processed_candle_time",
        "collector_last_success",
        "collector_last_error",
        "collector_consecutive_errors",
        "collector_total_errors",
        "stale_candle_count",
        "gap_event_count",
        "duplicate_attempt_count",
        "database_error_count",
        "position_open",
        "position_side",
        "current_equity",
        "updated_at",
        "last_error_message",
    )

    def __init__(self, run_id: str, **kwargs: Any) -> None:
        self.run_id = run_id
        self.mode: str = kwargs.get("mode", "")
        self.strategy_label: str = kwargs.get("strategy_label", "v3_candidate reference baseline")
        self.config_hash: str = kwargs.get("config_hash", "")
        self.commit_sha: str = kwargs.get("commit_sha", "")
        self.db_path: str = kwargs.get("db_path", "")
        self.last_processed_candle_time: str | None = kwargs.get("last_processed_candle_time")
        self.collector_last_success: str | None = kwargs.get("collector_last_success")
        self.collector_last_error: str | None = kwargs.get("collector_last_error")
        self.collector_consecutive_errors: int = int(kwargs.get("collector_consecutive_errors", 0))
        self.collector_total_errors: int = int(kwargs.get("collector_total_errors", 0))
        self.stale_candle_count: int = int(kwargs.get("stale_candle_count", 0))
        self.gap_event_count: int = int(kwargs.get("gap_event_count", 0))
        self.duplicate_attempt_count: int = int(kwargs.get("duplicate_attempt_count", 0))
        self.database_error_count: int = int(kwargs.get("database_error_count", 0))
        self.position_open: bool = bool(kwargs.get("position_open", False))
        self.position_side: str = kwargs.get("position_side", "")
        self.current_equity: float = float(kwargs.get("current_equity", 0.0))
        self.updated_at: str = kwargs.get("updated_at") or kwargs.get("heartbeat_time", "")
        self.last_error_message: str = kwargs.get("last_error_message", "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "strategy_label": self.strategy_label,
            "config_hash": self.config_hash,
            "commit_sha": self.commit_sha,
            "db_path": self.db_path,
            "last_processed_candle_time": self.last_processed_candle_time,
            "collector_last_success": self.collector_last_success,
            "collector_last_error": self.collector_last_error,
            "collector_consecutive_errors": self.collector_consecutive_errors,
            "collector_total_errors": self.collector_total_errors,
            "stale_candle_count": self.stale_candle_count,
            "gap_event_count": self.gap_event_count,
            "duplicate_attempt_count": self.duplicate_attempt_count,
            "database_error_count": self.database_error_count,
            "position_open": self.position_open,
            "position_side": self.position_side,
            "current_equity": self.current_equity,
            "updated_at": self.updated_at,
            "last_error_message": self.last_error_message,
        }
