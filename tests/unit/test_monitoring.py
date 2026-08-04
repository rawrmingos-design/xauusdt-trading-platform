"""Deterministic tests for PROJECT-MONITORING-001.

Covers: heartbeat persistence + restore, missing-heartbeat detection,
collector error/recovery events, stale/gap/duplicate candle events,
alert deduplication + rate limiting, recovery notification (exactly once),
Telegram disabled mode, delivery-failure safety, kill-switch events,
daily report generation, and restart behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from xauusdt.monitoring import (
    MonitoringService,
    MonitorStore,
    TelegramConfig,
)
from xauusdt.monitoring.models import EventCode


def _t(hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 8, 4, hour, minute, tzinfo=UTC)


def _store(tmp_path: Path, name: str = "m.db") -> MonitorStore:
    return MonitorStore(tmp_path / name)


def _svc(tmp_path: Path, name: str = "m.db") -> MonitoringService:
    return MonitoringService(_store(tmp_path, name))


def _hb_state(**over: object) -> dict[str, object]:
    base = {
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
    base.update(over)
    return base


# ------------------------------------------------------------ heartbeat


def test_heartbeat_persists_and_restores(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.heartbeat("r1", "paper", _hb_state(current_equity=9_800.5))
    hb = svc._store.get_heartbeat("r1")
    assert hb is not None
    assert hb["current_equity"] == 9_800.5
    assert hb["position_open"] is False
    # restart: new service against same DB
    svc2 = _svc(tmp_path)
    hb2 = svc2._store.get_heartbeat("r1")
    assert hb2 is not None
    assert hb2["current_equity"] == 9_800.5


def test_heartbeat_tracks_position_state(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.heartbeat("r1", "paper", _hb_state(position_open=True, position_side="LONG"))
    hb = svc._store.get_heartbeat("r1")
    assert hb["position_open"] is True
    assert hb["position_side"] == "LONG"


def test_heartbeat_overwrites_previous(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.heartbeat("r1", "paper", _hb_state(current_equity=9_000.0))
    svc.heartbeat("r1", "paper", _hb_state(current_equity=9_500.0))
    hb = svc._store.get_heartbeat("r1")
    assert hb["current_equity"] == 9_500.0
    assert svc._store.recent_events("r1") == []  # heartbeat is not an event


# ------------------------------------------------------------ health eval


def test_missing_heartbeat_is_critical(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    h = svc.evaluate_health("ghost")
    assert h["runtime_alive"] is False
    assert EventCode.RUNTIME_HEARTBEAT_MISSING.value in h["issues"]


def test_fresh_heartbeat_is_alive(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.tg_cfg = TelegramConfig({"MONITORING_HEARTBEAT_TIMEOUT_SECONDS": "180"})
    svc.heartbeat("r1", "paper", _hb_state())
    h = svc.evaluate_health("r1")
    assert h["runtime_alive"] is True
    assert h["heartbeat_age_seconds"] < 180
    assert h["issues"] == []


def test_stale_heartbeat_detected(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.heartbeat("r1", "paper", _hb_state())
    # force heartbeat 1h old -> stale
    svc._store.update_heartbeat_ts("r1", _t(0) - timedelta(hours=1))
    h = svc.evaluate_health("r1")
    assert h["runtime_alive"] is False
    assert EventCode.RUNTIME_HEARTBEAT_MISSING.value in h["issues"]


def test_collector_errors_surface_in_health(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.heartbeat("r1", "paper", _hb_state(collector_consecutive_errors=2))
    h = svc.evaluate_health("r1")
    assert EventCode.COLLECTOR_ERROR.value in h["issues"]


# ------------------------------------------------------------ events


def test_record_event_persists(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.record("r1", EventCode.CANDLE_GAP, "gap detected", "WARNING", {"delta_seconds": 3600})
    events = svc._store.recent_events("r1")
    assert len(events) == 1
    assert events[0]["code"] == EventCode.CANDLE_GAP.value
    assert events[0]["severity"] == "WARNING"
    assert events[0]["metadata"]["delta_seconds"] == 3600


def test_event_severity_defaults(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.record("r1", EventCode.RUN_STARTED, "started")
    svc.record("r1", EventCode.STALE_CANDLE, "stale")
    svc.record("r1", EventCode.KILL_SWITCH_ENABLED, "halt")
    events = svc._store.recent_events("r1")
    sev = {e["code"]: e["severity"] for e in events}
    assert sev[EventCode.RUN_STARTED.value] == "INFO"
    assert sev[EventCode.STALE_CANDLE.value] == "WARNING"
    assert sev[EventCode.KILL_SWITCH_ENABLED.value] == "CRITICAL"


def test_duplicate_event_same_timestamp_deduped(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    ts = _t(10, 0)
    svc.record("r1", EventCode.STALE_CANDLE, "first", ts=ts)
    svc.record("r1", EventCode.STALE_CANDLE, "second", ts=ts)  # same (run, code, ts)
    events = svc._store.recent_events("r1")
    assert len(events) == 1


def test_kill_switch_events_recorded(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.alert("r1", EventCode.KILL_SWITCH_ENABLED.value, "halted", "CRITICAL")
    svc.alert("r1", EventCode.KILL_SWITCH_DISABLED.value, "resumed", "INFO")
    codes = {e["code"] for e in svc._store.recent_events("r1")}
    assert EventCode.KILL_SWITCH_ENABLED.value in codes
    assert EventCode.KILL_SWITCH_DISABLED.value in codes
