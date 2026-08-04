#!/usr/bin/env python3
"""PROJECT-DATA-010: Extend OKX historical dataset + freeze dataset versions.

- Uses existing idempotent OKX backfill (_download_okx_all)
- Extends 15m (and optionally 1H) toward 365 days if OKX has history
- Does NOT change strategy configs
- Produces quality audit, REST-vs-DB spot checks, frozen manifests
- Registry under docs/datasets/ for list/select tooling
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from xauusdt.collectors.okx_backfill import _download_okx_all
from xauusdt.exchange.okx_client import OKXClient
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db

DEFAULT_DB = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
SYMBOL = "XAU-USDT-SWAP"
# Anchor end matches prior research cut (BT-017 / discovery end)
DATASET_END = datetime(2026, 7, 15, tzinfo=UTC)
# Period labels for future backtests (no strategy claims)
DISCOVERY_START = datetime(2026, 4, 16, tzinfo=UTC)
DISCOVERY_END = datetime(2026, 7, 15, tzinfo=UTC)
OOS_PRE_START = datetime(2026, 1, 16, tzinfo=UTC)  # BT-017 pre-discovery
OOS_PRE_END = DISCOVERY_START
# Forward post-discovery starts after last stored research candle day
FORWARD_START = datetime(2026, 7, 15, tzinfo=UTC)

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "docs" / "reports"
DATASET_DIR = REPO_ROOT / "docs" / "datasets"
GRAN_SECONDS = {
    "5m": 300,
    "15m": 900,
    "1H": 3600,
    "4H": 14400,
}


def fingerprint_range(
    symbol: str,
    granularity: str,
    start: datetime,
    end: datetime,
    count: int,
    gap_count: int,
    duplicate_count: int,
    first_iso: str | None,
    last_iso: str | None,
) -> str:
    raw = "|".join(
        [
            symbol,
            granularity,
            start.isoformat(),
            end.isoformat(),
            str(count),
            str(gap_count),
            str(duplicate_count),
            first_iso or "",
            last_iso or "",
            "okx-public-rest",
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def db_range_stats(session: Any, symbol: str, granularity: str) -> dict[str, Any]:
    res = await session.execute(
        text(
            "SELECT COUNT(*), MIN(open_time), MAX(open_time) "
            "FROM candles WHERE symbol=:s AND granularity=:g"
        ),
        {"s": symbol, "g": granularity},
    )
    row = res.fetchone()
    count = int(row[0] or 0)
    return {
        "count": count,
        "min_open_time": row[1],
        "max_open_time": row[2],
    }


async def audit_continuity(
    session: Any,
    symbol: str,
    granularity: str,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> dict[str, Any]:
    """Gap / duplicate / continuity audit over stored rows (optionally windowed)."""
    params: dict[str, Any] = {"s": symbol, "g": granularity}
    clauses = ["symbol=:s", "granularity=:g"]
    if range_start is not None:
        clauses.append("open_time >= :rs")
        params["rs"] = range_start
    if range_end is not None:
        clauses.append("open_time < :re")
        params["re"] = range_end

    res = await session.execute(
        text(f"SELECT open_time FROM candles WHERE {' AND '.join(clauses)} ORDER BY open_time"),
        params,
    )
    times = [r[0] for r in res.fetchall()]
    if not times:
        return {
            "valid": False,
            "actual_count": 0,
            "gap_count": 0,
            "duplicate_count": 0,
            "sample_gaps": [],
            "first": None,
            "last": None,
            "expected_count_span": 0,
            "coverage_pct": 0.0,
        }

    # Normalize tz-aware
    def _aware(t: datetime) -> datetime:
        if t.tzinfo is None:
            return t.replace(tzinfo=UTC)
        return t.astimezone(UTC)

    times = [_aware(t) for t in times]
    seconds = GRAN_SECONDS.get(granularity, 900)

    gap_count = 0
    sample_gaps: list[dict[str, Any]] = []
    for i in range(1, len(times)):
        diff = (times[i] - times[i - 1]).total_seconds()
        if diff > seconds * 1.5:
            gap_count += 1
            if len(sample_gaps) < 15:
                sample_gaps.append(
                    {
                        "after": times[i - 1].isoformat(),
                        "before": times[i].isoformat(),
                        "gap_seconds": diff,
                        "missing_candles": max(int(diff / seconds) - 1, 0),
                    }
                )

    counts = Counter(t.isoformat() for t in times)
    duplicates = sum(c - 1 for c in counts.values() if c > 1)

    span_start = times[0]
    span_end = times[-1] + timedelta(seconds=seconds)
    expected = int((span_end - span_start).total_seconds() / seconds)
    coverage = round(len(times) / expected * 100, 2) if expected > 0 else 0.0

    return {
        "valid": gap_count == 0 and duplicates == 0,
        "actual_count": len(times),
        "gap_count": gap_count,
        "duplicate_count": duplicates,
        "sample_gaps": sample_gaps,
        "first": times[0].isoformat(),
        "last": times[-1].isoformat(),
        "expected_count_span": expected,
        "coverage_pct": coverage,
        "seconds": seconds,
    }


async def rest_vs_db_spot(
    session: Any,
    client: OKXClient,
    symbol: str,
    granularity: str,
    sample_time: datetime,
) -> dict[str, Any]:
    """Compare up to 100 REST candles older than sample_time against DB OHLC."""
    candles = await client.fetch_candles(
        symbol=symbol,
        granularity=granularity,
        start_time=sample_time,
        limit=100,
    )
    if not candles:
        return {
            "sample_time": sample_time.isoformat(),
            "status": "skipped",
            "reason": "no_rest_candles",
            "okx_n": 0,
            "db_matched": 0,
            "mismatches": 0,
        }

    times = [c.open_time for c in candles]
    t_min, t_max = min(times), max(times)
    res = await session.execute(
        text(
            "SELECT open_time, open_price, high, low, close, volume "
            "FROM candles WHERE symbol=:s AND granularity=:g "
            "AND open_time >= :a AND open_time <= :b"
        ),
        {"s": symbol, "g": granularity, "a": t_min, "b": t_max},
    )
    db_map = {
        r[0].astimezone(UTC).isoformat() if r[0].tzinfo else r[0].replace(tzinfo=UTC).isoformat(): r
        for r in res.fetchall()
    }

    mismatches = 0
    matched = 0
    details: list[dict[str, Any]] = []
    for c in candles:
        key = c.open_time.astimezone(UTC).isoformat()
        row = db_map.get(key)
        if row is None:
            continue
        matched += 1
        if (
            abs(float(row[1]) - c.open) > 0.01
            or abs(float(row[2]) - c.high) > 0.01
            or abs(float(row[3]) - c.low) > 0.01
            or abs(float(row[4]) - c.close) > 0.01
        ):
            mismatches += 1
            if len(details) < 5:
                details.append(
                    {
                        "time": key,
                        "okx": [c.open, c.high, c.low, c.close],
                        "db": [float(row[1]), float(row[2]), float(row[3]), float(row[4])],
                    }
                )

    status = "pass" if mismatches == 0 and matched > 0 else ("fail" if mismatches else "skipped")
    return {
        "sample_time": sample_time.isoformat(),
        "status": status,
        "okx_n": len(candles),
        "db_matched": matched,
        "mismatches": mismatches,
        "mismatch_details": details,
    }


def write_manifest(
    version_id: str,
    symbol: str,
    granularity: str,
    start: datetime,
    end: datetime,
    audit: dict[str, Any],
    source: str,
    notes: list[str],
    periods: dict[str, Any],
    rest_checks: list[dict[str, Any]] | None = None,
    backfill: dict[str, Any] | None = None,
) -> Path:
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    fp = fingerprint_range(
        symbol,
        granularity,
        start,
        end,
        int(audit.get("actual_count", 0)),
        int(audit.get("gap_count", 0)),
        int(audit.get("duplicate_count", 0)),
        audit.get("first"),
        audit.get("last"),
    )
    created = datetime.now(UTC).isoformat()
    manifest = {
        "version_id": version_id,
        "task_id": "PROJECT-DATA-010",
        "created_at": created,
        "symbol": symbol,
        "granularity": granularity,
        "source": source,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "candle_count": audit.get("actual_count", 0),
        "gap_count": audit.get("gap_count", 0),
        "duplicate_count": audit.get("duplicate_count", 0),
        "continuity_valid": audit.get("valid", False),
        "coverage_pct": audit.get("coverage_pct"),
        "first_candle": audit.get("first"),
        "last_candle": audit.get("last"),
        "fingerprint": fp,
        "checksum": fp,  # deterministic content fingerprint (not full binary dump)
        "quality_checks": {
            "gap_audit": "pass" if audit.get("gap_count", 0) == 0 else "fail",
            "duplicate_audit": "pass" if audit.get("duplicate_count", 0) == 0 else "fail",
            "continuity_audit": "pass" if audit.get("valid") else "fail",
            "sample_gaps": audit.get("sample_gaps", []),
            "rest_vs_db": rest_checks or [],
        },
        "periods": periods,
        "backfill": backfill or {},
        "notes": notes,
        "strategy_unchanged": True,
        "production_ready": False,
    }
    path = DATASET_DIR / f"{version_id}.json"
    path.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
    return path


def periods_block(available_start: datetime, available_end: datetime) -> dict[str, Any]:
    return {
        "discovery": {
            "start": DISCOVERY_START.isoformat(),
            "end": DISCOVERY_END.isoformat(),
            "role": "contaminated_design_sample_BT011_016",
        },
        "pre_discovery_oos": {
            "start": OOS_PRE_START.isoformat(),
            "end": OOS_PRE_END.isoformat(),
            "role": "BT017_true_pre_discovery_oos",
        },
        "extended_pre_discovery": {
            "start": available_start.isoformat(),
            "end": OOS_PRE_START.isoformat(),
            "role": "extra_history_before_BT017_oos_for_BT018_multi_regime",
            "note": "Only if available_start < OOS_PRE_START",
        },
        "forward_oos_candidate": {
            "start": FORWARD_START.isoformat(),
            "end": None,
            "role": "post_discovery_forward_oos_BT019_when_enough_days",
            "note": "Do not use for edge verdict until >=30-60d post-discovery",
        },
        "available_span": {
            "start": available_start.isoformat(),
            "end": available_end.isoformat(),
        },
    }


async def run(
    db_url: str,
    days: int,
    granularities: list[str],
    skip_backfill: bool,
) -> dict[str, Any]:
    await init_db(db_url)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    end = DATASET_END
    start = end - timedelta(days=days)
    backfill_results: dict[str, Any] = {}
    audits: dict[str, Any] = {}
    rest_all: dict[str, list[dict[str, Any]]] = {}
    manifests: dict[str, str] = {}

    # Snapshot 180d slice before extension (must remain intact as subset)
    pre_180: dict[str, Any] = {}
    async for session in get_session():
        for g in granularities:
            pre_180[g] = await audit_continuity(session, SYMBOL, g, OOS_PRE_START, DISCOVERY_END)
        await session.close()
        break

    if not skip_backfill:
        print(f"Backfill target: {start.isoformat()} → {end.isoformat()} ({days}d)", flush=True)
        async with OKXClient() as client:
            async for session in get_session():
                repo = CandleRepository(session)
                for g in granularities:
                    print(f"  downloading {SYMBOL} {g}...", flush=True)
                    result = await _download_okx_all(client, repo, g, start, end, dry_run=False)
                    backfill_results[g] = result.to_dict()
                    print(
                        f"  {g}: downloaded={result.downloaded_count} "
                        f"stored={result.stored_count} gaps={result.gap_count} "
                        f"status={result.status}",
                        flush=True,
                    )
                await session.close()
                break
    else:
        print("Skip backfill — audit + freeze only", flush=True)

    # Full DB audits + REST spot checks
    async with OKXClient() as client:
        async for session in get_session():
            for g in granularities:
                stats = await db_range_stats(session, SYMBOL, g)
                full_audit = await audit_continuity(session, SYMBOL, g)
                audits[g] = {"db_stats": stats, "continuity": full_audit}

                # REST samples: near start of available, mid, near end
                samples: list[datetime] = []
                if full_audit.get("first") and full_audit.get("last"):
                    first = datetime.fromisoformat(full_audit["first"])
                    last = datetime.fromisoformat(full_audit["last"])
                    mid = first + (last - first) / 2
                    samples = [
                        first + timedelta(days=1),
                        mid,
                        last - timedelta(hours=6),
                    ]
                rest_checks: list[dict[str, Any]] = []
                for st in samples:
                    try:
                        chk = await rest_vs_db_spot(session, client, SYMBOL, g, st)
                        rest_checks.append(chk)
                        print(
                            f"  REST-vs-DB {g} @{st.date()}: {chk['status']} "
                            f"matched={chk['db_matched']} mism={chk['mismatches']}",
                            flush=True,
                        )
                    except Exception as exc:  # noqa: BLE001 — report, don't crash freeze
                        rest_checks.append(
                            {
                                "sample_time": st.isoformat(),
                                "status": "error",
                                "error": str(exc),
                            }
                        )
                rest_all[g] = rest_checks

                # Freeze full available span as version
                min_t = stats["min_open_time"]
                max_t = stats["max_open_time"]
                if min_t is None or max_t is None:
                    print(f"  WARN: no candles for {g}, skip manifest")
                    continue
                if min_t.tzinfo is None:
                    min_t = min_t.replace(tzinfo=UTC)
                if max_t.tzinfo is None:
                    max_t = max_t.replace(tzinfo=UTC)
                # end exclusive for span labeling uses max candle + 1 bar
                span_end = max_t + timedelta(seconds=GRAN_SECONDS.get(g, 900))
                day_span = int((span_end - min_t).total_seconds() // 86400)
                version_id = f"ds_{SYMBOL.replace('-', '').lower()}_{g}_{day_span}d_v1"
                # Normalize known aliases
                if g == "15m" and day_span >= 350:
                    version_id = "ds_xau_15m_365d_v1"
                elif g == "15m" and 170 <= day_span <= 190:
                    version_id = "ds_xau_15m_180d_v1"
                elif g == "15m" and day_span >= 250:
                    version_id = f"ds_xau_15m_{day_span}d_v1"

                path = write_manifest(
                    version_id=version_id,
                    symbol=SYMBOL,
                    granularity=g,
                    start=min_t,
                    end=span_end,
                    audit=full_audit,
                    source="okx-public-rest",
                    notes=[
                        "PROJECT-DATA-010 freeze. Strategy configs unchanged.",
                        "Do not re-fit strategies on this dataset in this task.",
                        "180d research span remains a valid subset of extended history.",
                    ],
                    periods=periods_block(min_t, span_end),
                    rest_checks=rest_checks,
                    backfill=backfill_results.get(g),
                )
                manifests[g] = str(path.relative_to(REPO_ROOT))
                print(f"  Manifest: {path}", flush=True)

            # Also freeze explicit 180d research slice for 15m (subset, no re-download)
            if "15m" in granularities:
                a180 = await audit_continuity(session, SYMBOL, "15m", OOS_PRE_START, DISCOVERY_END)
                p180 = write_manifest(
                    version_id="ds_xau_15m_180d_v1",
                    symbol=SYMBOL,
                    granularity="15m",
                    start=OOS_PRE_START,
                    end=DISCOVERY_END,
                    audit=a180,
                    source="okx-public-rest-subset",
                    notes=[
                        "Frozen 180d research window (BT-017 span).",
                        "Subset of extended store; not a separate physical table.",
                        "Pre-extension snapshot counts recorded for integrity check.",
                    ],
                    periods=periods_block(OOS_PRE_START, DISCOVERY_END),
                    rest_checks=None,
                    backfill={
                        "subset_of": manifests.get("15m"),
                        "pre_extension_audit": pre_180.get("15m"),
                    },
                )
                manifests["15m_180d"] = str(p180.relative_to(REPO_ROOT))
                print(f"  Manifest 180d: {p180}", flush=True)

            await session.close()
            break

    # Quality report
    report = {
        "task_id": "PROJECT-DATA-010",
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": SYMBOL,
        "target_days": days,
        "target_start": start.isoformat(),
        "target_end": end.isoformat(),
        "skip_backfill": skip_backfill,
        "backfill": backfill_results,
        "audits": {
            g: {
                "count": audits[g]["continuity"].get("actual_count"),
                "first": audits[g]["continuity"].get("first"),
                "last": audits[g]["continuity"].get("last"),
                "gap_count": audits[g]["continuity"].get("gap_count"),
                "duplicate_count": audits[g]["continuity"].get("duplicate_count"),
                "continuity_valid": audits[g]["continuity"].get("valid"),
                "coverage_pct": audits[g]["continuity"].get("coverage_pct"),
                "db_min": str(audits[g]["db_stats"].get("min_open_time")),
                "db_max": str(audits[g]["db_stats"].get("max_open_time")),
            }
            for g in audits
        },
        "rest_vs_db": rest_all,
        "manifests": manifests,
        "pre_extension_180d": {
            g: {
                "count": pre_180[g].get("actual_count"),
                "gaps": pre_180[g].get("gap_count"),
                "duplicates": pre_180[g].get("duplicate_count"),
            }
            for g in pre_180
        },
        "strategy_unchanged": True,
        "known_limitations": [
            "OKX public history-candles limited to 100 per request; pagination required.",
            "OKX may not retain full 365d for every instrument/granularity.",
            "Dataset versions are logical freezes (manifest + DB range), not separate DB clones.",
            "Forward post-discovery window still short (~19d as of early Aug 2026) — not for edge verdict.",
            "No strategy re-fit or promotion from this data task.",
        ],
        "recommended_next_steps": [
            "PROJECT-BACKTEST-018: multi-regime OOS on extended dataset, fixed v3_candidate vs s7b, no re-fit.",
            "PROJECT-BACKTEST-019: forward OOS harness post-discovery (smoke only until 30-60d).",
            "Keep collecting live 15m candles so forward window grows.",
        ],
    }

    json_path = REPORT_DIR / "data_quality_DATA-010.json"
    json_path.write_text(json.dumps(report, indent=2, default=str) + "\n")

    # Markdown report
    lines = [
        "# PROJECT-DATA-010: Extended Dataset + Freeze",
        "",
        f"**Generated**: {report['generated_at']}",
        f"**Symbol**: `{SYMBOL}`",
        f"**Target**: {days}d ending {end.date()} (best-effort if OKX shorter)",
        "",
        "## Status",
        "Data foundation only. **No strategy changes. No edge claims.**",
        "",
        "## Backfill commands",
        "```bash",
        f"uv run python tools/run_backfill.py --symbol {SYMBOL} --granularity 15m --days {days}",
        f"uv run python tools/run_data_010_extend.py --days {days}",
        "```",
        "",
        "## Available range & quality",
        "",
        "| Granularity | Count | First | Last | Gaps | Dupes | Continuity | Coverage |",
        "|---|---:|---|---|---:|---:|---|---:|",
    ]
    for g, a in report["audits"].items():
        lines.append(
            f"| {g} | {a['count']} | {a['first']} | {a['last']} | "
            f"{a['gap_count']} | {a['duplicate_count']} | "
            f"{'PASS' if a['continuity_valid'] else 'FAIL'} | {a['coverage_pct']}% |"
        )

    lines.extend(
        [
            "",
            "## REST-vs-DB spot checks",
            "",
        ]
    )
    for g, checks in rest_all.items():
        lines.append(f"### {g}")
        for c in checks:
            lines.append(
                f"- `{c.get('sample_time', '?')}`: **{c.get('status')}** "
                f"(okx={c.get('okx_n')}, matched={c.get('db_matched')}, "
                f"mismatches={c.get('mismatches', c.get('error', ''))})"
            )
        lines.append("")

    lines.extend(
        [
            "## Frozen manifests",
            "",
        ]
    )
    for k, p in manifests.items():
        lines.append(f"- `{k}` → `{p}`")

    lines.extend(
        [
            "",
            "## Period labels (for BT-018 / BT-019)",
            "",
            "| Period | Range | Role |",
            "|---|---|---|",
            f"| Discovery | {DISCOVERY_START.date()} → {DISCOVERY_END.date()} | Contaminated design sample |",
            f"| Pre-discovery OOS | {OOS_PRE_START.date()} → {OOS_PRE_END.date()} | BT-017 true OOS |",
            f"| Extended pre-OOS | available_start → {OOS_PRE_START.date()} | Extra multi-regime history |",
            f"| Forward OOS candidate | {FORWARD_START.date()} → … | BT-019; wait 30–60d for verdict |",
            "",
            "## 180d integrity",
            "Extended backfill uses upsert — existing 180d rows are not deleted. "
            "Pre-extension 180d audit snapshot is stored in the report JSON.",
            "",
            "## Dataset tooling",
            "```bash",
            "uv run python tools/list_dataset_versions.py",
            "uv run python tools/select_dataset.py --version ds_xau_15m_365d_v1",
            "```",
            "",
            "## Known limitations",
            "",
        ]
    )
    lines.extend(f"- {x}" for x in report["known_limitations"])
    lines.extend(["", "## Recommended next steps", ""])
    lines.extend(f"- {x}" for x in report["recommended_next_steps"])
    lines.extend(
        [
            "",
            f"JSON report: `{json_path.relative_to(REPO_ROOT)}`",
            "",
        ]
    )
    md_path = REPORT_DIR / "data_quality_DATA-010.md"
    md_path.write_text("\n".join(lines) + "\n")

    print(f"\nWrote {json_path}", flush=True)
    print(f"Wrote {md_path}", flush=True)
    return report


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PROJECT-DATA-010 extend + freeze")
    p.add_argument("--db-url", default=DEFAULT_DB)
    p.add_argument("--days", type=int, default=365, help="Target history length")
    p.add_argument(
        "--granularity",
        default="15m,1H",
        help="Comma-separated granularities (default 15m,1H)",
    )
    p.add_argument(
        "--skip-backfill",
        action="store_true",
        help="Only audit + write manifests from existing DB",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    grans = [g.strip() for g in args.granularity.split(",") if g.strip()]
    asyncio.run(run(args.db_url, args.days, grans, args.skip_backfill))
