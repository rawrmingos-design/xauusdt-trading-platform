#!/usr/bin/env bash
# xauusdt-deploy-metadata — record deployment metadata for the forward run.
#
# PROJECT-OPS-001: writes /var/lib/xauusdt/deployment.json with the deployed
# commit SHA, strategy config hash, run_id, forward-window metadata and the
# deployment timestamp. Used by rollback runbooks and forward-OOS evaluation.
#
# Usage:
#   xauusdt-deploy-metadata --run-id forward-paper-v3-candidate-20260716 \
#                           --config-hash <hash> \
#                           --commit <sha> \
#                           --forward-start 2026-07-16 \
#                           --state-dir /var/lib/xauusdt
set -euo pipefail

RUN_ID=""
CONFIG_HASH=""
COMMIT=""
FORWARD_START=""
STATE_DIR=""

usage() {
    sed -n '2,18p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        --config-hash) CONFIG_HASH="$2"; shift 2 ;;
        --commit) COMMIT="$2"; shift 2 ;;
        --forward-start) FORWARD_START="$2"; shift 2 ;;
        --state-dir) STATE_DIR="$2"; shift 2 ;;
        *) usage ;;
    esac
done

[[ -n "$RUN_ID" && -n "$STATE_DIR" ]] || { echo "ERROR: --run-id and --state-dir required" >&2; usage; }
mkdir -p "$STATE_DIR"

COMMIT=${COMMIT:-$(git -C "$(dirname "$0")/.." rev-parse HEAD 2>/dev/null || echo "unknown")}
CONFIG_HASH=${CONFIG_HASH:-"v3_candidate_frozen"}
FORWARD_START=${FORWARD_START:-"2026-07-16"}

cat > "$STATE_DIR/deployment.json" <<EOF
{
  "run_id": "$RUN_ID",
  "strategy_profile": "v3_candidate reference baseline",
  "config_hash": "$CONFIG_HASH",
  "commit_sha": "$COMMIT",
  "forward_start_utc": "${FORWARD_START}T00:00:00Z",
  "mode": "paper",
  "orders": "simulated only",
  "deployed_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "monitoring": "PROJECT-MONITORING-001",
  "risk_engine": "PROJECT-RISK-001",
  "supervisor": "systemd"
}
EOF
echo "deployment metadata: $STATE_DIR/deployment.json"
