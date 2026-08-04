# PROJECT-OPS-001 — Supervised Continuous Paper Runtime

Deploy the frozen `v3_candidate` paper runtime under systemd, restart-safe, with
online SQLite backups, integrity verification, restore drills, secrets handling,
and bounded automatic recovery.

> **Engineering status: paper observation only.** Redesign/promotion NOT allowed
> before the forward-OOS checkpoint (**2026-09-13**). Strategy, risk and entry/exit
> semantics are frozen. Do not retune on observed forward candles.

---

## 1. Architecture

```
        ┌─────────────────────────────── systemd ───────────────────────────────┐
        │                                                                    │
 /etc/xauusdt/paper.env (0600, root-owned)                            xauusdt-paper-health.timer (2min)
        │ env: MONITORING_TELEGRAM_*, XAUUSDT_RUN_ID, poll interval              │
        ▼                                                                    ▼
 xauusdt-paper.service (user=xauusdt, bounded restart)         xauusdt-paper-health.service
        │  ExecStart: xauusdt-paper paper --db ... --run-id ...               │  --check → exit 1 on unhealthy
        │                                                                     ▼
        ▼                                                              journald (+ OnFailure alerting)
 /var/lib/xauusdt/paper_runs.db  ── online backup ──►  /var/backups/xauusdt/
        │   reports/ , deployment.json                     paper_runs_<date>_<run>.db + .json
        ▲
 xauusdt-paper-backup.timer (03:15 UTC) → xauusdt-paper-backup.service
        │  VACUUM INTO + PRAGMA integrity_check + retention 30d
        ▼
 xauusdt-restore-drill.sh  (isolated path, never touches live db)
```

## 2. Files

| Path | Purpose | Owner/Mode |
|---|---|---|
| `/opt/xauusdt/.venv` | runtime venv | root / 0755 |
| `/opt/xauusdt/bin/*.sh` | backup / restore / metadata helpers | root / 0755 |
| `/etc/xauusdt/paper.env` | runtime secrets + overrides | root:xauusdt / **0600** |
| `/var/lib/xauusdt/paper_runs.db` | single-writer state DB | xauusdt / 0750 dir |
| `/var/lib/xauusdt/reports/` | daily reports | xauusdt / 0750 |
| `/var/lib/xauusdt/deployment.json` | deploy metadata | xauusdt / 0750 |
| `/var/backups/xauusdt/` | backups | xauusdt / 0750 |
| `/etc/systemd/system/xauusdt-paper*.{service,timer}` | units | root / 0644 |
| `/etc/systemd/journald.conf.d/xauusdt.conf` | log retention | root / 0644 |

## 3. Security controls (enforced)

- Service runs as **unprivileged `xauusdt`** (system user, nologin), never root.
- `paper.env` is **0600** root-owned; Telegram token only on the VPS, never committed.
- State/backup dirs **0750** — not world-readable.
- **No OKX private credentials** are configured anywhere; orders are simulated.
- systemd hardening: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`,
  `ProtectHome`, `ReadWritePaths` scoped, `RestrictSUIDSGID`, `LockPersonality`,
  `PrivateDevices`, `ProtectKernel*`, `RestrictRealtime`.
- Kill-switch remains operator-accessible via `xauusdt-paper risk kill-on` under
  the same run_id (does not require the service to be live).
- Backups include only the runtime DB — no unrelated user data.

---

## 4. Install (first time)

```bash
# clone + build (once)
git clone https://github.com/rawrmingos-design/xauusdt-trading-platform.git /home/devistopup13/xauusdt-platform
cd /home/devistopup13/xauusdt-platform

# installs venv, dirs, units, timers, metadata (idempotent)
sudo deploy/xauusdt-install.sh

# ---- edit secrets (REQUIRED before enabling alerts) ----
sudo editor /etc/xauusdt/paper.env
#   MONITORING_TELEGRAM_ENABLED=true
#   MONITORING_TELEGRAM_BOT_TOKEN=<token>      # never commit
#   MONITORING_TELEGRAM_CHAT_ID=<chat_id>

# verify the Telegram hook works
xauusdt-paper monitor test-telegram --db /var/lib/xauusdt/paper_runs.db --run-id forward-paper-v3-candidate-20260716

# ---- start service + timers ----
sudo systemctl enable --now xauusdt-paper.service
sudo systemctl enable --now xauusdt-paper-health.timer xauusdt-paper-backup.timer

# ---- harden log retention ----
sudo mkdir -p /etc/systemd/journald.conf.d
sudo install -m 0644 deploy/systemd/journald-xauusdt.conf /etc/systemd/journald.conf.d/
sudo systemctl restart systemd-journald

# ---- confirm health ----
sudo systemctl status xauusdt-paper.service
xauusdt-paper monitor health --db /var/lib/xauusdt/paper_runs.db --run-id forward-paper-v3-candidate-20260716
cat /var/lib/xauusdt/deployment.json
```

## 5. Update (same run_id, safe)

```bash
cd /home/devistopup13/xauusdt-platform && git pull
sudo deploy/xauusdt-install.sh        # rebuilds nothing if venv exists; refreshes units/scripts/metadata
sudo systemctl daemon-reload
sudo systemctl restart xauusdt-paper.service
# confirm: heartbeat healthy, run_id unchanged, no duplicates
xauusdt-paper monitor health --db /var/lib/xauusdt/paper_runs.db --run-id forward-paper-v3-candidate-20260716
```

> Deployment metadata records the new commit SHA in `deployment.json`; the run_id
> stays fixed so the forward window keeps the same identity.

## 6. Start / Stop / Restart / Status

```bash
sudo systemctl start   xauusdt-paper.service
sudo systemctl stop    xauusdt-paper.service       # graceful SIGTERM, persists state
sudo systemctl restart xauusdt-paper.service
sudo systemctl status  xauusdt-paper.service
sudo journalctl -u xauusdt-paper.service -f          # follow logs
sudo systemctl list-timers | grep xauusdt           # timers scheduled
```

## 7. Rollback

```bash
# 1. stop current, restore the DB from the freshest intact backup
sudo systemctl stop xauusdt-paper.service
sudo deploy/xauusdt-restore-drill.sh \
    --backup /var/backups/xauusdt/paper_runs_<latest>_forward-paper-v3-candidate-20260716.db \
    --out /tmp/rollback-verify          # verify integrity first (isolated path)

# 2. if verified, copy the intact snapshot over the live db (never during the restore drill)
#    The runtime is stopped, so copying over is safe here.
sudo cp /var/backups/xauusdt/paper_runs_<latest>_forward-paper-v3-candidate-20260716.db /var/lib/xauusdt/paper_runs.db
sudo chown xauusdt:xauusdt /var/lib/xauusdt/paper_runs.db

# 3. roll back the code if the failure was a bad deploy
cd /home/devistopup13/xauusdt-platform && git checkout <previous-good-sha>   # or git reset --hard
sudo deploy/xauusdt-install.sh

# 4. restart and confirm no duplicates
sudo systemctl start xauusdt-paper.service
xauusdt-paper monitor health --db /var/lib/xauusdt/paper_runs.db --run-id forward-paper-v3-candidate-20260716
```

## 8. Backup (manual)

```bash
sudo /opt/xauusdt/bin/xauusdt-backup.sh \
    --db /var/lib/xauusdt/paper_runs.db \
    --out /var/backups/xauusdt \
    --run-id forward-paper-v3-candidate-20260716 \
    --retention 30 --verbose
# Produces paper_runs_<date>_<run_id>.db + .json (integrity_check result inside)
```

## 9. Restore drill (isolated path — does NOT touch live db)

```bash
sudo /opt/xauusdt/bin/xauusdt-restore-drill.sh \
    --backup /var/backups/xauusdt/paper_runs_<date>_forward-paper-v3-candidate-20260716.db \
    --out /tmp/restore-drill
# Prints integrity + per-table row counts + DRIVE_COMPLETE <path>
```

## 10. Kill switch

```bash
# enable (persistent across service restart, stored in risk_state)
xauusdt-paper risk kill-on --db /var/lib/xauusdt/paper_runs.db --run-id forward-paper-v3-candidate-20260716 --reason "operator stop"
# disable
xauusdt-paper risk kill-off --db /var/lib/xauusdt/paper_runs.db --run-id forward-paper-v3-candidate-20260716
# inspect
xauusdt-paper risk show --db /var/lib/xauusdt/paper_runs.db --run-id forward-paper-v3-candidate-20260716
```

## 11. Health checks

```bash
# exit 0 = healthy, exit 1 = heartbeat missing / unhealthy (for the health timer)
xauusdt-paper monitor health --db /var/lib/xauusdt/paper_runs.db \
    --run-id forward-paper-v3-candidate-20260716 --check; echo "exit=$?"

xauusdt-paper monitor events --db /var/lib/xauusdt/paper_runs.db --run-id forward-paper-v3-candidate-20260716 --limit 20
xauusdt-paper monitor alerts --db /var/lib/xauusdt/paper_runs.db --run-id forward-paper-v3-candidate-20260716
```

---

## 12. Constraints (frozen)

- Strategy config **frozen** to `v3_candidate` — no overrides.
- Forward observations may **not** be used for redesign before the checkpoint.
- Monitoring is **observation-only**.
- Telegram delivery failure must **not** stop the service.
- Restart/system-boot must **not** duplicate signals, orders, fills, risk decisions,
  or monitoring events (dedup by `candle_time`; `run_id` is fixed).
- No real order path. All timestamps UTC.

## 13. Checkpoints

| Window | Date | Action |
|---|---|---|
| 30d | 2026-08-14 | operational observation only |
| 60d | 2026-09-13 | **preliminary forward-OOS evaluation (BACKTEST-019 / FORWARD-OOS-001)** |
| 90d | 2026-10-13 | stronger evidence |

No new filters are to be born from "two candles behaving rudely."