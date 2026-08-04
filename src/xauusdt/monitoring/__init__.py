"""Runtime health monitoring and Telegram alerting (PROJECT-MONITORING-001).

Observation-only: monitoring never alters strategy, risk, or execution
decisions. Telegram is optional and disabled by default; a failed delivery
never stops the paper harness.
"""

from xauusdt.monitoring.alerts import AlertManager, TelegramConfig, TelegramNotifier
from xauusdt.monitoring.models import (
    EventCode,
    Heartbeat,
    MonitoringEvent,
    Severity,
)
from xauusdt.monitoring.reports import (
    build_daily_snapshot,
    render_markdown,
    write_daily_report,
)
from xauusdt.monitoring.service import MonitoringService
from xauusdt.monitoring.store import MonitorStore

__all__ = [
    "AlertManager",
    "EventCode",
    "Heartbeat",
    "MonitorStore",
    "MonitoringEvent",
    "MonitoringService",
    "Severity",
    "TelegramConfig",
    "TelegramNotifier",
    "build_daily_snapshot",
    "render_markdown",
    "write_daily_report",
]
