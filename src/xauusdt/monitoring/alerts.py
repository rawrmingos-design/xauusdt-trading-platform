"""Telegram alert transport for PROJECT-MONITORING-001.

Design:
  - Credentials come ONLY from environment variables (MONITORING_TELEGRAM_*).
  - Telegram is optional and disabled by default.
  - A failed delivery never raises into the runner: exceptions are caught,
    recorded in monitor_telegram_delivery, and the run continues.
  - Repeated identical alerts are rate-limited by (run_id, code): a new
    notification is only sent when the alert was previously resolved, or when
    the alert cooldown (MONITORING_ALERT_COOLDOWN_SECONDS) has elapsed since
    the last send for the same code.
  - Recovery: when an active alert clears, exactly one RECOVERED notification
    is sent (distinct from repeated healthy heartbeats).
  - Untrusted messages (collector error text etc.) are escaped before sending.
"""

from __future__ import annotations

import html
import logging
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from xauusdt.monitoring.store import MonitorStore

log = logging.getLogger(__name__)

RECOVERY_SUFFIX = "_recovered"


class TelegramConfig:
    """Environment-driven config. All values default to disabled/empty."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        e = env if env is not None else os.environ
        self.enabled = e.get("MONITORING_TELEGRAM_ENABLED", "false").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        self.bot_token = e.get("MONITORING_TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = e.get("MONITORING_TELEGRAM_CHAT_ID", "").strip()
        try:
            self.alert_cooldown_seconds = int(e.get("MONITORING_ALERT_COOLDOWN_SECONDS", "900"))
        except ValueError:
            self.alert_cooldown_seconds = 900
        try:
            self.heartbeat_timeout_seconds = int(
                e.get("MONITORING_HEARTBEAT_TIMEOUT_SECONDS", "180")
            )
        except ValueError:
            self.heartbeat_timeout_seconds = 180

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.bot_token) and bool(self.chat_id)


class TelegramNotifier:
    """Rate-limited, failure-safe Telegram alert sender."""

    def __init__(
        self,
        store: MonitorStore,
        cfg: TelegramConfig | None = None,
        send: Any | None = None,
    ) -> None:
        self._store = store
        self._cfg = cfg or TelegramConfig()
        self._send = send  # injectable for tests

    def enabled(self) -> bool:
        return self._cfg.ready

    # ------------------------------------------------------------- sending
    def _deliver(self, text: str) -> tuple[bool, str]:
        """POST to Telegram Bot API. Returns (ok, detail). Never raises."""
        if not self._cfg.ready:
            return False, "telegram disabled"
        url = (
            f"https://api.telegram.org/bot{self._cfg.bot_token}"
            f"/sendMessage?chat_id={self._cfg.chat_id}&text={html.escape(text)}"
        )
        try:
            if self._send is not None:
                result = self._send(text)
                if isinstance(result, tuple):
                    ok, detail = result
                else:
                    ok, detail = bool(result), ""
                return ok, str(detail)
            with urllib.request.urlopen(url, timeout=10) as resp:
                return resp.status == 200, f"http {resp.status}"
        except urllib.error.HTTPError as e:
            return False, f"http {e.code}"
        except Exception as e:  # noqa: BLE001 - transport must not crash runner
            return False, f"{type(e).__name__}: {e}"

    # --------------------------------------------------------- rate limiting
    def _within_cooldown(self, run_id: str, code: str) -> bool:
        last = self._store.last_delivery(run_id, code)
        if last is None:
            return False
        try:
            ts = datetime.fromisoformat(last["timestamp"])
        except (ValueError, TypeError):
            return False
        return datetime.now(UTC) - ts < timedelta(seconds=self._cfg.alert_cooldown_seconds)

    def notify(
        self,
        run_id: str,
        code: str,
        severity: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Send one notification unless rate-limited. Never raises."""
        if not self._cfg.ready:
            return False, "telegram disabled"
        if self._within_cooldown(run_id, code):
            return False, "rate-limited"
        text = format_alert(run_id, code, severity, message, metadata or {})
        ok, detail = self._deliver(text)
        attempt = 1
        if not ok:
            # one retry
            ok, detail = self._deliver(text)
            attempt = 2
        self._store.record_delivery(run_id, code, severity, ok, attempt, detail)
        return ok, detail


# ---------------------------------------------------------------------------
# Message formatting (matches the spec's example shapes)
# ---------------------------------------------------------------------------
def format_alert(
    run_id: str,
    code: str,
    severity: str,
    message: str,
    metadata: dict[str, Any],
) -> str:
    sev = str(severity).upper()
    icon = "🔴" if sev == "CRITICAL" else "🟠" if sev == "WARNING" else "🟢"
    if code.endswith(RECOVERY_SUFFIX):
        # recovery notification: distinct shape, exactly once per condition
        component = str(metadata.get("Component", "component")) if metadata else "component"
        lines = [
            f"{icon} RECOVERED — Paper Runtime",
            "",
            f"Run: {run_id}",
            f"Component: {component}",
            f"Event: {code}",
        ]
        for k, v in (metadata or {}).items():
            lines.append(f"{k}: {html.escape(str(v))}")
        return "\n".join(lines)
    title = (
        "Paper Runtime"
        if code.startswith("monitor_runtime")
        else (
            "Risk"
            if code.startswith("monitor_") and "loss" in code or "kill" in code
            else "Paper Runtime"
        )
    )
    lines = [
        f"{icon} {sev} — {title}",
        "",
        f"Run: {run_id}",
        f"Event: {code}",
    ]
    if message:
        lines.append(f"Detail: {html.escape(message)}")
    for k, v in metadata.items():
        lines.append(f"{k}: {html.escape(str(v))}")
    return "\n".join(lines)


def format_recovery(run_id: str, code: str, component: str, metadata: dict[str, Any]) -> str:
    lines = [
        "🟢 RECOVERED — Paper Runtime",
        "",
        f"Run: {run_id}",
        f"Component: {component}",
    ]
    for k, v in metadata.items():
        lines.append(f"{k}: {html.escape(str(v))}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Recovery + dedup orchestration (uses MonitorStore alert lifecycle)
# ---------------------------------------------------------------------------
class AlertManager:
    """Tracks active alerts and decides when to notify / recover.

    - open_alert(run_id, code): notifies if first time OR cooldown elapsed;
      marks alert active.
    - clear_alert(run_id, code): marks inactive and sends ONE recovery
      notification if the alert was active.
    """

    def __init__(self, store: MonitorStore, notifier: TelegramNotifier | None = None) -> None:
        self._store = store
        self._notifier = notifier or TelegramNotifier(store)

    def should_alert(self, run_id: str, code: str, ts: datetime, cfg: TelegramConfig) -> bool:
        """Rate-limit check (test-friendly, injectable clock)."""
        last = self._store.last_delivery(run_id, code)
        if last is None:
            return True
        try:
            last_ts = datetime.fromisoformat(last["timestamp"])
        except (ValueError, TypeError):
            return True
        return ts - last_ts >= timedelta(seconds=cfg.alert_cooldown_seconds)

    def open_alert(
        self,
        run_id: str,
        code: str,
        severity: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        ts = datetime.now(UTC).isoformat()
        self._store.alert_open(run_id, code, severity, message, ts)
        # notify on first open; re-notify after cooldown (rate limit in notifier)
        return self._notifier.notify(run_id, code, severity, message, metadata)

    def clear_alert(self, run_id: str, code: str, component: str = "") -> tuple[bool, str]:
        was_active = self._store.alert_is_active(run_id, code)
        self._store.alert_resolve(run_id, code)
        if not was_active:
            return False, "not-active"
        hb = self._store.get_heartbeat(run_id) or {}
        meta = {
            "Downtime": "recovered",
            "Last candle": hb.get("last_processed_candle_time") or "unknown",
        }
        return self._notifier.notify(run_id, code + RECOVERY_SUFFIX, "INFO", "", meta)
