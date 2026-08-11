"""XAUUSDT Operations API — read-only observability/control-plane.

PROJECT-FORWARD-OOS-001 constraint: this API exposes OPERATIONAL data only.
It deliberately has NO endpoint for PnL, expectancy, profit factor, win rate,
drawdown, long/short breakdown, or equity curve. Those remain inaccessible
until the 60d evaluation checkpoint (2026-09-13T00:00:00Z) is reached and the
performance layer is explicitly unlocked.

The dashboard (Next.js) is a read-only client. paper_runs.db + runtime +
deployment metadata remain the source of truth; this API only reads them.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from xauusdt.forward_oos.evaluator import current_checkpoint
from xauusdt.forward_oos.manifest import CHECKPOINT_60D, FORWARD_START, audit_window

# --------------------------------------------------------------------------- config

DEFAULT_DB = "/var/lib/xauusdt/paper_runs.db"
DEFAULT_RUN_ID = "forward-paper-v3-candidate-20260716"
DEPLOYMENT_META = "/var/lib/xauusdt/deployment.json"


def _db_path() -> str:
    return os.environ.get("XAUUSDT_OPS_DB", DEFAULT_DB)


def _run_id() -> str:
    return os.environ.get("XAUUSDT_OPS_RUN_ID", DEFAULT_RUN_ID)


def _auth_token() -> str:
    """Bearer token for the ops API. Env override; dev fallback is random."""
    return os.environ.get("XAUUSDT_OPS_TOKEN", "") or secrets.token_hex(32)


# --------------------------------------------------------------------------- app

bearer = HTTPBearer(auto_error=False)

app = FastAPI(
    title="XAUUSDT Operations API",
    version="1.0.0",
    description="Read-only operational observability for the XAUUSDT paper runtime. "
    "No performance metrics are exposed while evaluation is locked.",
)


class OpsToken(BaseModel):
    token: str


def _authorize(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    if not secrets.compare_digest(creds.credentials, _auth_token()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")


# --------------------------------------------------------------------------- read helpers


def _load_deployment() -> dict[str, Any]:
    p = Path(os.environ.get("XAUUSDT_OPS_DEPLOYMENT", DEPLOYMENT_META))
    import json

    try:
        if not p.exists():
            return {}
        loaded: Any = json.loads(p.read_text())
        return dict(loaded) if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, OSError, PermissionError):
        return {}


def _read_conn() -> sqlite3.Connection:
    """Open a strictly read-only connection to the live paper DB.

    The API is observation-only by construction: it must never CREATE/ALTER
    tables or write a journal entry into the production DB. `mode=ro` refuses
    any write (including schema DDL the store classes run in __init__).
    """
    uri = f"file:{_db_path()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _heartbeat_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Operational heartbeat summary — never includes performance numbers."""
    row = conn.execute("SELECT * FROM monitor_heartbeat WHERE run_id=?", (_run_id(),)).fetchone()
    if row is None:
        return {"present": False}
    return {
        "present": True,
        "updated_at": row["updated_at"],
        "collector_last_success": row["collector_last_success"],
        "collector_consecutive_errors": row["collector_consecutive_errors"],
        "collector_total_errors": row["collector_total_errors"],
        "stale_candle_count": row["stale_candle_count"],
        "last_processed_candle": row["last_processed_candle_time"],
        "position_open": bool(row["position_open"]),
        "position_side": row["position_side"],
    }


def _candle_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """Data-quality audit on stored candles (coverage/gaps/dupes only)."""
    from xauusdt.exchange.models import Candle

    rows = conn.execute(
        "SELECT symbol, open_time, open, high, low, close, volume, granularity "
        "FROM paper_candles WHERE run_id=? ORDER BY open_time",
        (_run_id(),),
    ).fetchall()
    if not rows:
        return {"rows": 0, "coverage_pct": 0.0, "gaps": 0, "duplicates": 0}
    candles = [
        Candle(
            symbol=r["symbol"],
            granularity=r["granularity"],
            open_time=datetime.fromisoformat(r["open_time"]),
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
        )
        for r in rows
    ]
    end = min(datetime.now(UTC), CHECKPOINT_60D)
    aud = audit_window(_run_id(), candles, FORWARD_START, end)
    return {
        "rows": len(candles),
        "coverage_pct": aud.coverage_pct,
        "gaps": aud.gap_count,
        "duplicates": aud.duplicate_count,
        "first_candle": candles[0].open_time.isoformat(),
        "latest_candle": candles[-1].open_time.isoformat(),
    }


def _recent_events(conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    """Latest monitor events (audit log), newest first."""
    rows = conn.execute(
        "SELECT id, run_id, code, severity, message, timestamp, metadata "
        "FROM monitor_events WHERE run_id=? ORDER BY timestamp DESC LIMIT ?",
        (_run_id(), limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        md: Any = {}
        try:
            import json

            md = json.loads(r["metadata"] or "{}")
        except ValueError:
            md = {}
        out.append(
            {
                "id": r["id"],
                "code": r["code"],
                "severity": r["severity"],
                "message": r["message"],
                "timestamp": r["timestamp"],
                "metadata": md,
            }
        )
    return out


# --------------------------------------------------------------------------- routes


@app.get("/api/health", dependencies=[Depends(_authorize)])
def api_health() -> dict[str, Any]:
    conn = _read_conn()
    try:
        # systemd / service state via db presence, not subprocess
        hb = _heartbeat_snapshot(conn)
        issues: list[str] = []
        if not hb.get("present"):
            issues.append("heartbeat_missing")
        else:
            try:
                from datetime import datetime as _dt

                updated = _dt.fromisoformat(hb["updated_at"])
                age = (datetime.now(UTC) - updated).total_seconds()
                if age > 120:
                    issues.append("heartbeat_stale")
            except (TypeError, ValueError):
                issues.append("heartbeat_unparseable")

        # backup current check (file mtime < 26h old)
        import glob

        backups = sorted(glob.glob("/var/backups/xauusdt/paper_runs_*_*.db"), reverse=True)
        backup_age_h = None
        if backups:
            mtime = datetime.fromtimestamp(Path(backups[0]).stat().st_mtime, tz=UTC)
            backup_age_h = round((datetime.now(UTC) - mtime).total_seconds() / 3600, 1)
        if backup_age_h is None or backup_age_h > 26:
            issues.append("backup_stale")

        return {
            "status": "degraded" if issues else "healthy",
            "issues": issues,
            "heartbeat": hb,
            "backup": {"latest": backups[0] if backups else None, "age_hours": backup_age_h},
            "db": {"path": _db_path()},
            "generated_utc": datetime.now(UTC).isoformat(),
        }
    finally:
        conn.close()


@app.get("/api/data-quality", dependencies=[Depends(_authorize)])
def api_data_quality() -> dict[str, Any]:
    conn = _read_conn()
    try:
        return {
            "run_id": _run_id(),
            "audit": _candle_audit(conn),
            "generated_utc": datetime.now(UTC).isoformat(),
        }
    finally:
        conn.close()


@app.get("/api/deployment", dependencies=[Depends(_authorize)])
def api_deployment() -> dict[str, Any]:
    meta = _load_deployment()
    cp = current_checkpoint()
    return {
        "deployment": meta,
        "forward_start_utc": FORWARD_START.isoformat(),
        "checkpoint_60d_utc": CHECKPOINT_60D.isoformat(),
        "evaluation": {
            "locked": cp is None,
            "unlock_at_utc": CHECKPOINT_60D.isoformat(),
        },
        "runtime_observation_start": meta.get("runtime_observation_start"),
        "generated_utc": datetime.now(UTC).isoformat(),
    }


@app.get("/api/audit", dependencies=[Depends(_authorize)])
def api_audit(limit: int = 50) -> dict[str, Any]:
    if limit < 1 or limit > 500:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "limit must be 1..500")
    conn = _read_conn()
    try:
        events = _recent_events(conn, limit=limit)
        return {
            "events": events,
            "count": len(events),
            "generated_utc": datetime.now(UTC).isoformat(),
        }
    finally:
        conn.close()


@app.get("/api/overview", dependencies=[Depends(_authorize)])
def api_overview() -> dict[str, Any]:
    """Consolidated operational state for the dashboard landing page."""
    conn = _read_conn()
    try:
        hb = _heartbeat_snapshot(conn)
        issues: list[str] = []
        if not hb.get("present"):
            issues.append("heartbeat_missing")
        else:
            try:
                from datetime import datetime as _dt

                updated = _dt.fromisoformat(hb["updated_at"])
                age = (datetime.now(UTC) - updated).total_seconds()
                if age > 120:
                    issues.append("heartbeat_stale")
            except (TypeError, ValueError):
                issues.append("heartbeat_unparseable")

        # backup current check (file mtime < 26h old)
        import glob

        backups = sorted(glob.glob("/var/backups/xauusdt/paper_runs_*_*.db"), reverse=True)
        backup_age_h = None
        if backups:
            mtime = datetime.fromtimestamp(Path(backups[0]).stat().st_mtime, tz=UTC)
            backup_age_h = round((datetime.now(UTC) - mtime).total_seconds() / 3600, 1)
        if backup_age_h is None or backup_age_h > 26:
            issues.append("backup_stale")

        # evaluation lock
        cp = current_checkpoint()
        # merge data-quality + deployment
        try:
            dq = _candle_audit(conn)
        except Exception:
            dq = {}
        meta = _load_deployment()
        return {
            "status": "degraded" if issues else "healthy",
            "issues": issues,
            "overview": {
                "heartbeat": hb,
                "data_quality": dq,
                "deployment": meta,
                "backup": {"latest": backups[0] if backups else None, "age_hours": backup_age_h},
                "evaluation": {
                    "locked": cp is None,
                    "unlock_at_utc": CHECKPOINT_60D.isoformat(),
                },
            },
            "generated_utc": datetime.now(UTC).isoformat(),
        }
    finally:
        conn.close()


@app.get("/api/token", include_in_schema=False)
def api_token_info() -> dict[str, Any]:
    """Non-sensitive token metadata (existence only, never the value)."""
    return {"configured": bool(os.environ.get("XAUUSDT_OPS_TOKEN"))}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8090)
