"""Tests for the read-only operations API (PROJECT-OPS-004).

Verifies:
  - bearer auth is enforced (401 without/with wrong token)
  - operational endpoints return expected shapes
  - NO performance field leaks (expectancy, PF, win rate, drawdown, PnL,
    equity curve, long/short breakdown) in any response while locked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xauusdt.ops_api.main import app

# --------------------------------------------------------------------------- fixtures

PERF_LEAK_TERMS = [
    "expectancy",
    "profit_factor",
    "win_rate",
    "drawdown",
    "net_pnl",
    "equity_curve",
    "long_short",
    "long_count",
    "short_count",
    "trade_count",
    "exit_reasons",
]


def _dump(obj: object) -> str:
    return json.dumps(obj, default=str).lower()


def assert_no_perf_leak(payload: object) -> None:
    blob = _dump(payload)
    for term in PERF_LEAK_TERMS:
        assert term not in blob, f"PERFORMANCE LEAK: {term!r} present in response"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """Client against a temp DB copy using a fixed token."""
    import os
    import sqlite3

    # build a minimal live-shaped DB: monitor tables + paper tables
    db = tmp_path / "ops.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE monitor_heartbeat (
            run_id TEXT PRIMARY KEY, mode TEXT, strategy_label TEXT,
            config_hash TEXT, commit_sha TEXT, db_path TEXT,
            last_processed_candle_time TEXT, collector_last_success TEXT,
            collector_last_error TEXT, collector_consecutive_errors INTEGER,
            collector_total_errors INTEGER, stale_candle_count INTEGER,
            gap_event_count INTEGER, duplicate_attempt_count INTEGER,
            database_error_count INTEGER, position_open INTEGER,
            position_side TEXT, current_equity REAL, last_error_message TEXT,
            updated_at TEXT
        );
        CREATE TABLE monitor_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, code TEXT,
            severity TEXT, message TEXT, timestamp TEXT, metadata TEXT
        );
        CREATE TABLE paper_runs (
            run_id TEXT PRIMARY KEY, mode TEXT, strategy_version TEXT,
            config_hash TEXT, commit_sha TEXT, candles_processed INTEGER,
            initial_balance REAL, final_balance REAL, total_pnl REAL,
            max_drawdown REAL, max_drawdown_pct REAL, created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE paper_candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            symbol TEXT, granularity TEXT, open_time TEXT, open REAL,
            high REAL, low REAL, close REAL, volume REAL
        );
        """
    )
    conn.execute(
        "INSERT INTO monitor_heartbeat VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "forward-paper-v3-candidate-20260716",
            "paper",
            "v3_candidate",
            "abc123",
            "deadbeef",
            "/tmp/ops.db",
            "2026-08-11T09:45:00+00:00",
            "2026-08-11T10:00:00+00:00",
            "",
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            "LONG",
            10000.0,
            "",
            "2026-08-11T10:00:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO monitor_events (run_id, code, severity, message, timestamp, metadata) "
        "VALUES (?,?,?,?,?,?)",
        (
            "forward-paper-v3-candidate-20260716",
            "monitor_run_started",
            "INFO",
            "paper run started",
            "2026-08-05T03:21:26+00:00",
            '{"mode":"paper"}',
        ),
    )
    conn.execute(
        "INSERT INTO paper_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "forward-paper-v3-candidate-20260716",
            "paper",
            "v3_candidate",
            "abc123",
            "deadbeef",
            100,
            10000.0,
            10100.0,
            100.0,
            50.0,
            0.5,
            "2026-08-05T03:21:26+00:00",
            "2026-08-11T10:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    os.environ["XAUUSDT_OPS_DB"] = str(db)
    os.environ["XAUUSDT_OPS_TOKEN"] = "test-token"
    os.environ["XAUUSDT_OPS_DEPLOYMENT"] = str(tmp_path / "deployment.json")
    (tmp_path / "deployment.json").write_text(
        json.dumps({"commit_sha": "deadbeef", "config_hash": "abc123"})
    )
    return TestClient(app)


# --------------------------------------------------------------------------- auth


def test_requires_bearer(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 401
    r = client.get("/api/health", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_endpoints_accept_token(client: TestClient) -> None:
    h = {"Authorization": "Bearer test-token"}
    for ep in ["health", "overview", "data-quality", "deployment", "audit"]:
        r = client.get(f"/api/{ep}", headers=h)
        assert r.status_code == 200, f"{ep} -> {r.status_code}: {r.text[:200]}"


# --------------------------------------------------------------------------- no perf leak


def test_no_performance_leak_all_endpoints(client: TestClient) -> None:
    h = {"Authorization": "Bearer test-token"}
    for ep in ["health", "overview", "data-quality", "deployment", "audit"]:
        r = client.get(f"/api/{ep}", headers=h)
        assert r.status_code == 200
        assert_no_perf_leak(r.json())


def test_data_quality_shape(client: TestClient) -> None:
    h = {"Authorization": "Bearer test-token"}
    r = client.get("/api/data-quality", headers=h)
    body = r.json()
    assert "audit" in body
    assert "rows" in body["audit"]
    assert "coverage_pct" in body["audit"]
    assert "gaps" in body["audit"]
    assert "duplicates" in body["audit"]


def test_deployment_shape(client: TestClient) -> None:
    h = {"Authorization": "Bearer test-token"}
    r = client.get("/api/deployment", headers=h)
    body = r.json()
    assert body["evaluation"]["locked"] is True
    assert "forex_start" not in json.dumps(body)
    assert body["checkpoint_60d_utc"]


def test_audit_limit_bound(client: TestClient) -> None:
    h = {"Authorization": "Bearer test-token"}
    assert client.get("/api/audit?limit=0", headers=h).status_code == 400
    assert client.get("/api/audit?limit=9999", headers=h).status_code == 400
    r = client.get("/api/audit?limit=5", headers=h)
    assert r.status_code == 200
    assert len(r.json()["events"]) <= 5


def test_health_shape(client: TestClient) -> None:
    h = {"Authorization": "Bearer test-token"}
    r = client.get("/api/health", headers=h)
    body = r.json()
    assert body["status"] in ("healthy", "degraded")
    assert isinstance(body["issues"], list)
    assert body["heartbeat"]["present"] is True
