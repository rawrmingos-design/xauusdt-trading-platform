# XAUUSDT Operations Dashboard

Observability / control-plane dashboard for the XAUUSDT paper platform
(PROJECT-OPS-004). **Read-only by construction**: it never writes to the
paper DB, and it has no performance endpoints while the forward-OOS
evaluation is locked.

## Architecture

```
paper DB ──► ops-api (FastAPI, read-only sqlite) ──► Next.js dashboard
                  │  auth: bearer token               │  server-side fetch
                  │  no perf endpoints                │  token stays on server
```

- `src/xauusdt/ops_api/` — FastAPI app. SQLite connections use
  `mode=ro` URI so writes (including schema DDL) are refused by SQLite
  itself, not just by convention.
- `dashboard/` — Next.js (App Router, `output: standalone`). All data is
  fetched server-side with the bearer token; `OPS_TOKEN` is never exposed
  to the browser (`NEXT_PUBLIC_*` is intentionally unused).

## Endpoints (all require `Authorization: Bearer <token>`)

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | runtime alive, heartbeat age, backup age, issues |
| `GET /api/overview` | consolidated landing state |
| `GET /api/data-quality` | candle coverage / gaps / duplicates |
| `GET /api/deployment` | commit, config hash, forward start, eval lock |
| `GET /api/audit?limit=N` | recent monitor events |

**No performance data** (PnL, expectancy, PF, win rate, drawdown, equity
curve, long/short) is served — by design, until the 60D checkpoint unlocks.

## Local development

```bash
# 1. ops-api (env: XAUUSDT_OPS_TOKEN, XAUUSDT_OPS_DB, XAUUSDT_OPS_DEPLOYMENT)
uv run uvicorn xauusdt.ops_api.main:app --port 8090

# 2. dashboard
cd dashboard
OPS_API_URL=http://127.0.0.1:8090 OPS_TOKEN=dev-token npm run dev
```

## Deployment (VPS)

`sudo deploy/xauusdt-install.sh` installs the ops-api + dashboard systemd
units (plus env files under `/etc/xauusdt/`). Then:

```bash
sudo systemctl enable --now xauusdt-ops-api.service xauusdt-dashboard.service
```

The dashboard env (`/etc/xauusdt/ops-api.env`) is shared by both services;
`XAUUSDT_OPS_TOKEN` authenticates dashboard → ops-api calls.
