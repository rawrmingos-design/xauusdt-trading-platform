#!/usr/bin/env bash
# xauusdt-backup — SQLite-safe online backup for the continuous paper runtime.
#
# PROJECT-OPS-001: uses the SQLite online backup API (VACUUM INTO), which is
# safe while the runtime holds the database open. Produces:
#   <backup_dir>/paper_runs_<UTC-date>_<run_id>.db
#   <backup_dir>/paper_runs_<UTC-date>_<run_id>.json   (metadata + integrity result)
#
# Retention: keeps the newest N daily backups (default 30), deletes older ones.
#
# Usage:
#   xauusdt-backup --db /var/lib/xauusdt/paper_runs.db \
#                  --out /var/backups/xauusdt \
#                  --run-id forward-paper-v3-candidate-20260716 \
#                  [--retention 30] [--verbose]
set -euo pipefail

DB=""
OUT=""
RUN_ID=""
RETENTION=30
VERBOSE=0

usage() {
    sed -n '2,30p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db) DB="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --run-id) RUN_ID="$2"; shift 2 ;;
        --retention) RETENTION="$2"; shift 2 ;;
        --verbose) VERBOSE=1; shift ;;
        *) usage ;;
    esac
done

[[ -n "$DB" && -n "$OUT" && -n "$RUN_ID" ]] || { echo "ERROR: --db, --out, --run-id required" >&2; usage; }
[[ -f "$DB" ]] || { echo "ERROR: db not found: $DB" >&2; exit 1; }

mkdir -p "$OUT"
DATE=$(date -u +%Y%m%d)
BASE="paper_runs_${DATE}_${RUN_ID}"
BACKUP="$OUT/${BASE}.db"
META="$OUT/${BASE}.json"

# 1. Online backup via SQLite VACUUM INTO (atomic, safe while live).
sqlite3 "$DB" "VACUUM INTO '$BACKUP'" || { echo "ERROR: VACUUM INTO failed" >&2; exit 1; }

# 2. Integrity verification of the completed backup.
INTEGRITY=$(sqlite3 "$BACKUP" "PRAGMA integrity_check;" 2>&1 || true)
if [[ "$INTEGRITY" != "ok" ]]; then
    echo "ERROR: backup integrity check failed: $INTEGRITY" >&2
    rm -f "$BACKUP"
    exit 1
fi

# 3. Metadata: timestamp, source run_id, size, integrity result.
SIZE=$(stat -c %s "$BACKUP" 2>/dev/null || stat -f %z "$BACKUP")
SHA256=$(sha256sum "$BACKUP" | awk '{print $1}')
cat > "$META" <<EOF
{
  "backup_file": "$BACKUP",
  "created_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source_db": "$DB",
  "run_id": "$RUN_ID",
  "size_bytes": $SIZE,
  "sha256": "$SHA256",
  "integrity_check": "$INTEGRITY",
  "tool": "xauusdt-backup (PROJECT-OPS-001)"
}
EOF

# 4. Retention: keep newest N backups for this run_id, drop older ones.
mapfile -t OLD < <(ls -1t "${OUT}"/paper_runs_*_"${RUN_ID}".db 2>/dev/null | tail -n +$((RETENTION + 1)))
if [[ ${#OLD[@]} -gt 0 ]]; then
    for f in "${OLD[@]}"; do
        if [[ $VERBOSE -eq 1 ]]; then echo "retention: removing $f"; fi
        rm -f "$f" "${f%.db}.json"
    done
fi

if [[ $VERBOSE -eq 1 ]]; then
    echo "backup: $BACKUP ($SIZE bytes)"
    echo "meta:   $META"
    echo "integrity: $INTEGRITY"
    echo "retention: kept ${RETENTION}, removed ${#OLD[@]}"
fi
echo "$BACKUP"
