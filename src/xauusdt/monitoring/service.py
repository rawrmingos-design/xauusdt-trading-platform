"""Monitoring service for PROJECT-MONITORING-001.

Ties heartbeat persistence, event recording, health evaluation, and alert/
recovery notification together. This is the single facade the paper runner
and CLI use. Observation-only: it never touches strategy, risk, or execution
decision state besides reading it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from xauusdt.monitoring.alerts import AlertManager, TelegramConfig, TelegramNotifier
from xauusdt.monitoring.models import EventCode, MonitoringEvent, Severity
from xauusdt.monitoring.store import MonitorStore

log = logging.getLogger(__name__)


class MonitoringService:
    """Facade over MonitorStore + Telegram for the harness/runner/CLI."""

    def __init__(
        self,
        store: MonitorStore,
        telegram_cfg: TelegramConfig | None = None,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._store = store
        self.tg_cfg = telegram_cfg or TelegramConfig()
        self._notifier = notifier or TelegramNotifier(store, self.tg_cfg)
        self._alerts = AlertManager(store, self._notifier)

    # ------------------------------------------------------------ heartbeat
    def heartbeat(
        self,
        run_id: str,
        mode: str,
        state: dict[str, Any],
        *,
        strategy_label: str = "v3_candidate reference baseline",
        config_hash: str = "",
        commit_sha: str = "",
        db_path: str = "",
    ) -> None:
        """Persist a fresh heartbeat for an active run."""
        hb = {
            "run_id": run_id,
            "mode": mode,
            "strategy_label": strategy_label,
            "config_hash": config_hash,
            "commit_sha": commit_sha,
            "db_path": db_path,
            "last_processed_candle_time": state.get("last_processed_candle"),
            "collector_last_success": state.get("collector_last_success"),
            "collector_last_error": state.get("collector_last_error"),
            "collector_consecutive_errors": int(state.get("collector_consecutive_errors", 0)),
            "collector_total_errors": int(state.get("collector_total_errors", 0)),
            "stale_candle_count": int(state.get("stale_candle_count", 0)),
            "gap_event_count": int(state.get("gap_event_count", 0)),
            "duplicate_attempt_count": int(state.get("duplicate_attempt_count", 0)),
            "database_error_count": int(state.get("database_error_count", 0)),
            "position_open": bool(state.get("position_open", False)),
            "position_side": state.get("position_side", ""),
            "current_equity": float(state.get("current_equity", 0.0)),
            "last_error_message": state.get("last_error_message", ""),
        }
        self._store.upsert_heartbeat(hb)

    # -------------------------------------------------------------- events
    def record(
        self,
        run_id: str,
        code: str | EventCode,
        message: str = "",
        severity: str | None = None,
        metadata: dict[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> None:
        ev = MonitoringEvent(run_id, code, severity, message, ts, metadata)
        self._store.record_event(ev.to_dict())

    # ------------------------------------------------------------- alerts
    def alert(
        self,
        run_id: str,
        code: str,
        message: str,
        severity: str,
        metadata: dict[str, Any] | None = None,
        record_event: bool = True,
    ) -> None:
        if record_event:
            self.record(run_id, code, message, severity, metadata)
        self._alerts.open_alert(run_id, code, severity, message, metadata)

    def recover(
        self,
        run_id: str,
        code: str,
        component: str,
        message: str = "",
    ) -> None:
        self.record(run_id, EventCode.COLLECTOR_RECOVERED, message, Severity.INFO)
        self._alerts.clear_alert(run_id, code, component)

    def run_started(self, run_id: str, mode: str) -> None:
        self.alert(
            run_id,
            EventCode.RUN_STARTED.value,
            f"{mode} run started",
            Severity.INFO.value,
            {"mode": mode},
        )

    def run_stopped(self, run_id: str, mode: str, reason: str = "stopped") -> None:
        self.alert(
            run_id,
            EventCode.RUN_STOPPED.value,
            f"{mode} run {reason}",
            Severity.INFO.value,
            {"mode": mode, "reason": reason},
        )

    # ------------------------------------------------------------ health
    def evaluate_health(self, run_id: str) -> dict[str, Any]:
        """Evaluate current health for a run. Missing heartbeat is CRITICAL.

        Returns a dict with runtime_alive, heartbeat_age_seconds, and the
        list of triggered issue codes. Does not notify — caller decides.
        """
        now = datetime.now(UTC)
        hb = self._store.get_heartbeat(run_id)
        if hb is None:
            return {
                "runtime_alive": False,
                "heartbeat_age_seconds": None,
                "last_processed_candle_time": None,
                "stale_candle_count": 0,
                "gap_event_count": 0,
                "duplicate_attempt_count": 0,
                "database_error_count": 0,
                "position_open": False,
                "position_side": "",
                "current_equity": 0.0,
                "issues": [EventCode.RUNTIME_HEARTBEAT_MISSING.value],
            }
        try:
            updated = datetime.fromisoformat(hb["updated_at"])
        except (ValueError, TypeError):
            updated = now
        age = (now - updated).total_seconds()
        alive = age < self.tg_cfg.heartbeat_timeout_seconds
        issues: list[str] = []
        if not alive:
            issues.append(EventCode.RUNTIME_HEARTBEAT_MISSING.value)
        if int(hb["collector_consecutive_errors"]) >= 1:
            issues.append(EventCode.COLLECTOR_ERROR.value)
        return {
            "runtime_alive": alive,
            "heartbeat_age_seconds": round(age, 1),
            "last_processed_candle_time": hb.get("last_processed_candle_time"),
            "stale_candle_count": int(hb["stale_candle_count"]),
            "gap_event_count": int(hb["gap_event_count"]),
            "duplicate_attempt_count": int(hb["duplicate_attempt_count"]),
            "database_error_count": int(hb["database_error_count"]),
            "position_open": bool(hb["position_open"]),
            "position_side": hb["position_side"],
            "current_equity": float(hb["current_equity"]),
            "collector_consecutive_errors": int(hb["collector_consecutive_errors"]),
            "collector_total_errors": int(hb["collector_total_errors"]),
            "issues": issues,
        }

    def snapshot_state(self, run_id: str) -> dict[str, Any]:
        """Cross-section of required metrics for reports/health CLI."""
        hb = self._store.get_heartbeat(run_id)
        issues = self.evaluate_health(run_id)
        return {
            "runtime_alive": issues["runtime_alive"],
            "heartbeat_age_seconds": issues["heartbeat_age_seconds"],
            "last_processed_candle_time": issues["last_processed_candle_time"],
            "candle_age_seconds": None,
            "collector_consecutive_errors": hb["collector_consecutive_errors"] if hb else 0,
            "collector_total_errors": hb["collector_total_errors"] if hb else 0,
            "stale_candle_count": issues["stale_candle_count"],
            "gap_event_count": issues["gap_event_count"],
            "duplicate_attempt_count": issues["duplicate_attempt_count"],
            "database_error_count": issues["database_error_count"],
            "position_open": issues["position_open"],
            "position_side": issues["position_side"],
            "current_equity": issues["current_equity"],
            "issues": issues["issues"],
        }

    # ---------------------------------------------------- collector facade
    def record_collector_error(self, run_id: str, consecutive: int = 1) -> None:
        """Record a collector failure (WARNING, rate-limited) + update heartbeat."""
        self.alert(
            run_id,
            EventCode.COLLECTOR_ERROR.value,
            f"collector poll failed (consecutive={consecutive})",
            Severity.WARNING.value,
            {"consecutive_errors": consecutive},
        )
        hb = self._store.get_heartbeat(run_id)
        if hb is not None:
            hb["collector_consecutive_errors"] = int(hb["collector_consecutive_errors"]) + 1
            hb["collector_total_errors"] = int(hb["collector_total_errors"]) + 1
            hb["collector_last_error"] = datetime.now(UTC).isoformat()
            self._store.upsert_heartbeat(hb)

    def record_collector_recovery(self, run_id: str) -> None:
        """Record collector recovery (exactly-once RECOVERED notification)."""
        hb = self._store.get_heartbeat(run_id)
        if hb is not None:
            hb["collector_consecutive_errors"] = 0
            hb["collector_last_success"] = datetime.now(UTC).isoformat()
            self._store.upsert_heartbeat(hb)
        self.recover(run_id, EventCode.COLLECTOR_ERROR.value, "OKX candle collector")

    # -------------------------------------------------------- cycle facade
    def check_and_notify(self, run_id: str) -> None:
        """Evaluate health + trigger alerts/recovery for the current cycle.

        Called once per poll cycle by the runner (observation-only).
        """
        h = self.evaluate_health(run_id)
        if not h["runtime_alive"]:
            self.alert(
                run_id,
                EventCode.RUNTIME_HEARTBEAT_MISSING.value,
                "heartbeat missing",
                Severity.CRITICAL.value,
                {"heartbeat_age_seconds": h["heartbeat_age_seconds"]},
            )
        else:
            self.recover(
                run_id,
                EventCode.RUNTIME_HEARTBEAT_MISSING.value,
                "paper runtime",
            )
        if EventCode.COLLECTOR_ERROR.value in h["issues"]:
            self.alert(
                run_id,
                EventCode.COLLECTOR_ERROR.value,
                "collector degraded",
                Severity.WARNING.value,
                {"consecutive_errors": h["collector_consecutive_errors"]},
            )
        else:
            self.recover(run_id, EventCode.COLLECTOR_ERROR.value, "OKX candle collector")

    def alert_deliveries(self, run_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._store.alert_deliveries(run_id, limit)
