#!/usr/bin/env bash
# xauusdt-install.sh — install/update the systemd supervised paper runtime.
#
# PROJECT-OPS-001. Run as root (sudo). Idempotent: safe to re-run on update.
#
#   sudo deploy/xauusdt-install.sh
#
# Creates:
#   /opt/xauusdt            venv + app checkout (or symlink to an existing one)
#   /etc/xauusdt/paper.env  secrets (0600, root-owned; NOT created if absent)
#   /var/lib/xauusdt        state (db, reports, deployment.json)
#   /var/backups/xauusdt    backups
#   systemd units + timers
#
# Update flow (same script): replaces binaries, restarts the service.
set -euo pipefail

APP_SRC="${XAUUSDT_APP_SRC:-/home/devistopup13/xauusdt-platform}"
INSTALLER_HOME="/home/devistopup13"
INSTALL_DIR="${XAUUSDT_INSTALL_DIR:-/opt/xauusdt}"
STATE_DIR="${XAUUSDT_STATE_DIR:-/var/lib/xauusdt}"
BACKUP_DIR="${XAUUSDT_BACKUP_DIR:-/var/backups/xauusdt}"
CONF_DIR="/etc/xauusdt"
ENV_FILE="$CONF_DIR/paper.env"
RUN_ID="${XAUUSDT_RUN_ID:-forward-paper-v3-candidate-20260716}"
SERVICE="xauusdt-paper.service"

echo "== PROJECT-OPS-001 installer =="

# --- 1. runtime user ---
if ! id -u xauusdt >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin xauusdt
    echo "created user xauusdt (system, nologin)"
fi

# --- 2. directories + permissions ---
mkdir -p "$INSTALL_DIR" "$STATE_DIR/reports" "$BACKUP_DIR" "$CONF_DIR"
chown -R xauusdt:xauusdt "$STATE_DIR" "$BACKUP_DIR"
chmod 0750 "$STATE_DIR" "$BACKUP_DIR" "$STATE_DIR/reports"

# --- 3. application (venv) ---
# Resolve a Python satisfying project requires-python (>=3.11) AND supporting
# venv (ensurepip). python3.10 is excluded: pyproject requires Python>=3.11
# (datetime.UTC etc). If the venv does not exist, build it from this Python.
PY_BIN=""
for cand in python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1 \
       && "$cand" -c "import ensurepip" >/dev/null 2>&1 \
       && "$cand" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
        PY_BIN="$(command -v "$cand")"
        break
    fi
done

if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
    echo "building venv from $APP_SRC ..."
    if [[ -n "$PY_BIN" ]]; then
        "$PY_BIN" -m venv "$INSTALL_DIR/.venv"
        if command -v "$INSTALL_DIR/.venv/bin/pip" >/dev/null 2>&1; then
            "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
        fi
    elif command -v uv >/dev/null 2>&1; then
        echo "no python with ensurepip found; using uv (project .python-version: 3.12)"
        UV_PY="$(uv python find 2>/dev/null || true)"
        uv venv --python "${UV_PY:-3.12}" "$INSTALL_DIR/.venv"
    else
        echo "ERROR: no usable Python (need ensurepip) and no uv. Install python3-venv." >&2
        exit 1
    fi
fi

# --- 3b. install/refresh the application package (ALWAYS, even if venv exists)
# Non-editable: the venv must be self-contained. The source tree lives under a
# private home (mode 750) the service user cannot traverse; an editable install
# would break at runtime with ModuleNotFoundError. Reinstall on every run so a
# re-deploy ships the current repository commit even when the venv pre-exists.
INSTALL_VENV_PY="$INSTALL_DIR/.venv/bin/python"
if ! "$INSTALL_VENV_PY" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
    echo "ERROR: deployment venv Python is <3.11; rebuild the venv from a >=3.11 interpreter ($PY_BIN)." >&2
    exit 1
fi
if command -v uv >/dev/null 2>&1; then
    # uv resolves the hatchling build backend correctly.
    uv pip install --python "$INSTALL_VENV_PY" --force-reinstall --no-deps "$APP_SRC"
elif [[ -x "$INSTALLER_HOME/.local/bin/uv" ]]; then
    # sudo secure_path often omits ~/.local/bin; resolve uv explicitly.
    "$INSTALLER_HOME/.local/bin/uv" pip install --python "$INSTALL_VENV_PY" --force-reinstall --no-deps "$APP_SRC"
elif "$INSTALL_VENV_PY" -c "import pip" >/dev/null 2>&1; then
    "$INSTALL_VENV_PY" -m pip install --force-reinstall --no-deps "$APP_SRC" >/dev/null
else
    echo "ERROR: no uv (PATH or ~/.local/bin) and no pip in venv; cannot install package." >&2
    exit 1
fi
if ! "$INSTALL_VENV_PY" -c "import xauusdt"; then
    echo "ERROR: application package import failed after install; refusing to continue." >&2
    exit 1
fi
chown -R root:root "$INSTALL_DIR"
chmod -R o-w "$INSTALL_DIR" 2>/dev/null || chmod 0755 "$INSTALL_DIR"

# --- 4. env file (only if absent — secrets never overwritten) ---
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<EOF
# XAUUSDT paper runtime secrets (PROJECT-OPS-001). Root-owned, mode 0600.
# Real Telegram values are set on the VPS only; never commit them.
MONITORING_TELEGRAM_ENABLED=false
MONITORING_TELEGRAM_BOT_TOKEN=
MONITORING_TELEGRAM_CHAT_ID=
XAUUSDT_RUN_ID=$RUN_ID
XAUUSDT_POLL_INTERVAL=120
XAUUSDT_PAPER_CMD=
EOF
    chown root:xauusdt "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
    echo "created $ENV_FILE (0600) — EDIT IT to set Telegram secrets"
else
    echo "env file exists: $ENV_FILE (kept as-is)"
fi

# --- 5. helper scripts ---
mkdir -p /opt/xauusdt/bin
install -m 0755 "$APP_SRC/deploy/xauusdt-backup.sh" /opt/xauusdt/bin/
install -m 0755 "$APP_SRC/deploy/xauusdt-restore-drill.sh" /opt/xauusdt/bin/
install -m 0755 "$APP_SRC/deploy/xauusdt-deploy-metadata.sh" /opt/xauusdt/bin/

# --- 6. systemd units ---
install -m 0644 "$APP_SRC"/deploy/systemd/xauusdt-paper.service /etc/systemd/system/
install -m 0644 "$APP_SRC"/deploy/systemd/xauusdt-paper-health.service /etc/systemd/system/
install -m 0644 "$APP_SRC"/deploy/systemd/xauusdt-paper-health.timer /etc/systemd/system/
install -m 0644 "$APP_SRC"/deploy/systemd/xauusdt-paper-backup.service /etc/systemd/system/
install -m 0644 "$APP_SRC"/deploy/systemd/xauusdt-paper-backup.timer /etc/systemd/system/

systemctl daemon-reload

# --- 7. deployment metadata ---
/opt/xauusdt/bin/xauusdt-deploy-metadata.sh \
    --run-id "$RUN_ID" \
    --state-dir "$STATE_DIR" \
    --commit "$(git -C "$APP_SRC" rev-parse HEAD 2>/dev/null || echo unknown)" \
    --config-hash "$(cd "$APP_SRC" && .venv/bin/python -c 'from xauusdt.strategy.confluence import make_v3_candidate_config; from xauusdt.execution.paper.harness import config_hash; print(config_hash(make_v3_candidate_config()))' 2>/dev/null || echo v3_candidate_frozen)" \
    --forward-start 2026-07-16 || true

echo
echo "== done. Next:"
echo "  1. sudo editor $ENV_FILE   (set Telegram secrets, run_id, poll interval)"
echo "  2. sudo systemctl enable --now $SERVICE"
echo "  3. sudo systemctl enable --now xauusdt-paper-health.timer xauusdt-paper-backup.timer"
echo "  4. sudo journalctl -u $SERVICE -f"
