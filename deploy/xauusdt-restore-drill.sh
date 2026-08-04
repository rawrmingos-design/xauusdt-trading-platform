#!/usr/bin/env bash
# xauusdt-restore-drill — restore a backup into an ISOLATED path for verification.
#
# PROJECT-OPS-001 restore drill: never touches the live database.
# Restores <backup>.db into <out>/<basename>_restored.db, runs integrity_check,
# and reports row counts for paper/risk/monitoring tables so an operator can
# confirm the backup is restorable.
#
# Usage:
#   xauusdt-restore-drill --backup /var/backups/xauusdt/paper_runs_20260805_<run>.db \
#                         --out /tmp/restore-drill \
#                         [--run-id RUN_ID]
set -euo pipefail

BACKUP=""
OUT=""
RUN_ID=""

usage() {
    sed -n '2,20p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup) BACKUP="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --run-id) RUN_ID="$2"; shift 2 ;;
        *) usage ;;
    esac
done

[[ -n "$BACKUP" && -n "$OUT" ]] || { echo "ERROR: --backup and --out required" >&2; usage; }
[[ -f "$BACKUP" ]] || { echo "ERROR: backup not found: $BACKUP" >&2; exit 1; }

mkdir -p "$OUT"
RESTORED="$OUT/$(basename "$BACKUP" .db)_restored.db"

# 1. Copy the backup file (it is already a consistent SQLite snapshot).
cp "$BACKUP" "$RESTORED"

# 2. Verify integrity.
INTEGRITY=$(sqlite3 "$RESTORED" "PRAGMA integrity_check;" 2>&1 || true)
if [[ "$INTEGRITY" != "ok" ]]; then
    echo "ERROR: restored db failed integrity: $INTEGRITY" >&2
    exit 1
fi

# 3. Row counts per table (best-effort: tables may not all exist).
echo "restored: $RESTORED"
echo "integrity: $INTEGRITY"
for t in paper_runs paper_positions paper_trades paper_signals paper_decisions risk_state risk_decisions monitor_heartbeat monitor_events monitor_alert_state monitor_telegram_delivery; do
    if sqlite3 "$RESTORED" "SELECT 1 FROM $t LIMIT 1;" >/dev/null 2>&1; then
        COUNT=$(sqlite3 "$RESTORED" "SELECT COUNT(*) FROM $t;")
        echo "  $t: $COUNT rows"
    fi
done
echo "DRIVE_COMPLETE $RESTORED"
