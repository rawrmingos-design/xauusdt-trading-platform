"""Part 2: alert deduplication, rate limiting, recovery, Telegram transport."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from xauusdt.monitoring import (
    MonitoringService,
    MonitorStore,
    TelegramConfig,
    TelegramNotifier,
)
from xauusdt.monitoring.alerts import AlertManager
from xauusdt.monitoring.models import EventCode


def _t(hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 8, 4, hour, minute, tzinfo=UTC)


def _store(tmp_path: Path, name: str = "m.db") -> MonitorStore:
    return MonitorStore(tmp_path / name)


def _svc(tmp_path: Path, name: str = "m.db") -> MonitoringService:
    return MonitoringService(_store(tmp_path, name))


# ------------------------------------------------------------ AlertManager


def test_alert_manager_dedup_same_code_within_cooldown(tmp_path: Path) -> None:
    mgr = AlertManager(_store(tmp_path))
    cfg = TelegramConfig(
        {"MONITORING_ALERT_COOLDOWN_SECONDS": "900", "MONITORING_HEARTBEAT_TIMEOUT_SECONDS": "180"}
    )
    assert mgr.should_alert("r1", "monitor_collector_error", _t(10, 0), cfg) is True
    # simulate delivery at 10:00
    mgr._store.record_delivery(
        "r1", "monitor_collector_error", "WARNING", True, ts=_t(10, 0).isoformat()
    )
    # within 15-min cooldown -> suppressed
    assert mgr.should_alert("r1", "monitor_collector_error", _t(10, 5), cfg) is False
    assert mgr.should_alert("r1", "monitor_collector_error", _t(10, 14), cfg) is False
    # after cooldown (>=900s) -> allowed again
    assert mgr.should_alert("r1", "monitor_collector_error", _t(10, 15), cfg) is True


def test_alert_manager_fires_after_cooldown_elapsed(tmp_path: Path) -> None:
    mgr = AlertManager(_store(tmp_path))
    cfg = TelegramConfig(
        {"MONITORING_ALERT_COOLDOWN_SECONDS": "60", "MONITORING_HEARTBEAT_TIMEOUT_SECONDS": "180"}
    )
    assert mgr.should_alert("r1", "monitor_collector_error", _t(10, 0), cfg) is True
    mgr._store.record_delivery(
        "r1", "monitor_collector_error", "WARNING", True, ts=_t(10, 0).isoformat()
    )
    assert (
        mgr.should_alert("r1", "monitor_collector_error", _t(10, 0) + timedelta(seconds=59), cfg)
        is False
    )
    assert (
        mgr.should_alert("r1", "monitor_collector_error", _t(10, 0) + timedelta(seconds=61), cfg)
        is True
    )


def test_alert_manager_different_codes_independent(tmp_path: Path) -> None:
    mgr = AlertManager(_store(tmp_path))
    cfg = TelegramConfig(
        {"MONITORING_ALERT_COOLDOWN_SECONDS": "900", "MONITORING_HEARTBEAT_TIMEOUT_SECONDS": "180"}
    )
    assert mgr.should_alert("r1", "monitor_collector_error", _t(10, 0), cfg) is True
    assert mgr.should_alert("r1", "monitor_stale_candle", _t(10, 0), cfg) is True
    mgr._store.record_delivery(
        "r1", "monitor_collector_error", "WARNING", True, ts=_t(10, 0).isoformat()
    )
    assert mgr.should_alert("r1", "monitor_collector_error", _t(10, 1), cfg) is False
    assert mgr.should_alert("r1", "monitor_stale_candle", _t(10, 1), cfg) is True


def test_alert_manager_persists_across_restart(tmp_path: Path) -> None:
    mgr1 = AlertManager(_store(tmp_path))
    cfg = TelegramConfig(
        {"MONITORING_ALERT_COOLDOWN_SECONDS": "900", "MONITORING_HEARTBEAT_TIMEOUT_SECONDS": "180"}
    )
    assert mgr1.should_alert("r1", "monitor_collector_error", _t(10, 0), cfg) is True
    mgr1._store.record_delivery(
        "r1", "monitor_collector_error", "WARNING", True, ts=_t(10, 0).isoformat()
    )
    # restart with same DB
    mgr2 = AlertManager(_store(tmp_path))
    assert mgr2.should_alert("r1", "monitor_collector_error", _t(10, 5), cfg) is False


# ------------------------------------------------------------ Telegram


def test_telegram_disabled_without_credentials(tmp_path: Path) -> None:
    cfg = TelegramConfig({})  # MONITORING_TELEGRAM_ENABLED unset -> disabled
    assert cfg.enabled is False
    n = TelegramNotifier(_store(tmp_path), cfg)
    ok, detail = n.notify("r1", "monitor_collector_error", "WARNING", "x", {})
    assert ok is False
    assert "disabled" in detail.lower()


def test_telegram_delivery_failure_does_not_crash(tmp_path: Path) -> None:
    cfg = TelegramConfig(
        {
            "MONITORING_TELEGRAM_ENABLED": "true",
            "MONITORING_TELEGRAM_BOT_TOKEN": "bad:token",
            "MONITORING_TELEGRAM_CHAT_ID": "123",
        }
    )
    n = TelegramNotifier(_store(tmp_path), cfg, send=lambda text: (False, "boom"))
    ok, detail = n.notify("r1", "monitor_collector_error", "WARNING", "x", {})
    assert ok is False
    assert "boom" in detail


def test_telegram_delivery_records_attempt(tmp_path: Path) -> None:
    cfg = TelegramConfig(
        {
            "MONITORING_TELEGRAM_ENABLED": "true",
            "MONITORING_TELEGRAM_BOT_TOKEN": "tok",
            "MONITORING_TELEGRAM_CHAT_ID": "123",
        }
    )
    store = _store(tmp_path)
    n = TelegramNotifier(store, cfg, send=lambda text: (True, "ok"))
    ok, detail = n.notify("r1", "monitor_collector_error", "WARNING", "x", {})
    assert ok is True
    attempts = store.alert_deliveries("r1")
    assert len(attempts) == 1
    assert attempts[0]["ok"] == 1  # SQLite stores as int
    assert attempts[0]["code"] == "monitor_collector_error"


def test_telegram_escapes_message(tmp_path: Path) -> None:
    sent: list[str] = []

    def send(text: str) -> tuple[bool, str]:
        sent.append(text)
        return True, "ok"

    cfg = TelegramConfig(
        {
            "MONITORING_TELEGRAM_ENABLED": "true",
            "MONITORING_TELEGRAM_BOT_TOKEN": "tok",
            "MONITORING_TELEGRAM_CHAT_ID": "123",
        }
    )
    n = TelegramNotifier(_store(tmp_path), cfg, send=send)
    n.notify("r1", "monitor_collector_error", "WARNING", "boom <b>&", {})
    body = sent[0]
    # untrusted message must not be able to inject HTML
    assert "<b>" not in body.replace("&lt;b&gt;", "")
    assert "&lt;b&gt;" in body


# ------------------------------------------------------------ recovery


def test_recovery_notification_exactly_once(tmp_path: Path) -> None:
    """Active alert -> clear -> recovery fires exactly once per condition."""
    calls: list[str] = []
    cfg = TelegramConfig(
        {
            "MONITORING_TELEGRAM_ENABLED": "true",
            "MONITORING_TELEGRAM_BOT_TOKEN": "tok",
            "MONITORING_TELEGRAM_CHAT_ID": "123",
        }
    )
    svc = MonitoringService(
        _store(tmp_path),
        cfg,
        TelegramNotifier(_store(tmp_path), cfg, send=lambda text: (calls.append(text), True)[1]),
    )
    # start healthy
    svc.heartbeat("r1", "paper", _hb())
    svc.check_and_notify("r1")
    assert calls == []
    # heartbeat goes stale -> critical alert
    svc._store.update_heartbeat_ts("r1", _t(0) - timedelta(hours=1))
    svc.check_and_notify("r1")
    assert any("monitor_runtime_heartbeat_missing" in c for c in calls), calls
    # still stale -> no spam
    n_before = len(calls)
    svc.check_and_notify("r1")
    assert len(calls) == n_before
    # heartbeat restored -> recovery exactly once
    svc.heartbeat("r1", "paper", _hb())
    svc.check_and_notify("r1")
    assert any("RECOVERED" in c for c in calls), calls
    n_after = len(calls)
    svc.check_and_notify("r1")
    assert len(calls) == n_after  # no duplicate recovery


def _hb() -> dict[str, object]:
    return {
        "last_processed_candle": "2026-08-04T10:00:00+00:00",
        "collector_last_success": "2026-08-04T10:00:00+00:00",
        "collector_last_error": None,
        "collector_consecutive_errors": 0,
        "collector_total_errors": 0,
        "stale_candle_count": 0,
        "gap_event_count": 0,
        "duplicate_attempt_count": 0,
        "database_error_count": 0,
        "position_open": False,
        "position_side": "",
        "current_equity": 10_000.0,
        "last_error_message": "",
    }


def test_collector_error_then_recovery(tmp_path: Path) -> None:
    calls: list[str] = []
    cfg = TelegramConfig(
        {
            "MONITORING_TELEGRAM_ENABLED": "true",
            "MONITORING_TELEGRAM_BOT_TOKEN": "tok",
            "MONITORING_TELEGRAM_CHAT_ID": "123",
        }
    )
    svc = MonitoringService(
        _store(tmp_path),
        cfg,
        TelegramNotifier(_store(tmp_path), cfg, send=lambda text: (calls.append(text), True)[1]),
    )
    svc.heartbeat("r1", "paper", _hb())
    # collector fails
    svc.record_collector_error("r1")
    svc.check_and_notify("r1")
    assert any("monitor_collector_error" in c for c in calls)
    # recovers
    svc.record_collector_recovery("r1")
    svc.check_and_notify("r1")
    assert any("RECOVERED" in c for c in calls)
    assert any("collector" in c.lower() for c in calls)


def test_events_survive_restart(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.record("r1", EventCode.STALE_CANDLE, "stale", ts=_t(10))
    svc2 = _svc(tmp_path)  # restart
    events = svc2._store.recent_events("r1")
    assert len(events) == 1
    assert events[0]["code"] == EventCode.STALE_CANDLE.value
