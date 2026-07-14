#!/usr/bin/env bash
# Fix VPS system time synchronization and verify it works.
#
# Prerequisites: sudo access (for timedatectl/apt).
# Usage: sudo bash tools/fix_time.sh
#
# Exit codes:
#   0 — Time synchronized successfully
#   1 — Fix failed or NTP still not active
#   2 — Insufficient permissions or unsupported OS

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# --- Pre-flight checks ---
if [[ $EUID -ne 0 ]]; then
    error "This script requires root (run with sudo)."
    error "Usage: sudo bash tools/fix_time.sh"
    exit 2
fi

# Check NTP status
echo "=== Current time state ==="
timedatectl status 2>/dev/null || {
    error "timedatectl not available. Is systemd installed?"
    exit 1
}
echo ""

# Check current NTP state
ntp_active=false
if timedatectl show --property=NTP --value 2>/dev/null | grep -qi 'yes\|true'; then
    ntp_active=true
fi

ntp_synchronized=false
if timedatectl show --property=LocalRTC --value 2>/dev/null; then
    true  # skip
fi
if timedatectl status 2>/dev/null | grep -qi 'synchronized: yes'; then
    ntp_synchronized=true
fi

echo "Current NTP status:"
echo "  NTP enabled/active: $(timedatectl show --property=NTP --value 2>/dev/null)"
echo "  Synchronized:       $(timedatectl show --property=NetworkTimeSyncd --value 2>/dev/null || timedatectl status 2>/dev/null | grep 'synchronized' || echo 'unknown')"
echo ""

# --- Step 1: Enable systemd-timesyncd if not present ---
if ! command -v systemctl &>/dev/null; then
    # Likely not systemd-based (Alpine, etc.)
    warn "systemctl not found. Attempting OpenRC/SysVinit time fix..."
    # Check if ntpd or other NTP daemon is available
    if command -v ntpd &>/dev/null; then
        info "Starting ntpd..."
        ntpd -q && stop_nptd 2>/dev/null || warn "ntpd -q failed"
    elif command -v chronyd &>/dev/null; then
        info "Running chronyd -q..."
        chronyd -q || warn "chronyd -q failed"
    elif command -v hwclock &>/dev/null; then
        warn "No NTP daemon detected. Attempting manual sync..."
        date -u "$(date -u +%m%d%H%M%Y.%S)" || warn "Manual date set failed"
    else
        error "No NTP daemon found. Install one manually:"
        error "  apt install -y systemd-timesyncd  (Debian/Ubuntu)"
        error "  apt install -y chrony              (Debian/Ubuntu)"
        error "  apk add --no-cache openntpd         (Alpine)"
        exit 1
    fi
    exit 0
fi

# Determine package manager
PKG_MANAGER=""
if command -v apt-get &>/dev/null; then
    PKG_MANAGER="apt"
elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf"
elif command -v yum &>/dev/null; then
    PKG_MANAGER="yum"
elif command -v apk &>/dev/null; then
    PKG_MANAGER="apk"
fi

TIMESYNCD_PKG="systemd-timesyncd"
if [[ "$PKG_MANAGER" == "apk" ]]; then
    TIMESYNCD_PKG="openntpd"
fi

if [[ -n "$PKG_MANAGER" ]] && ! systemctl is-active systemd-timesyncd &>/dev/null; then
    info "Installing $TIMESYNCD_PKG..."
    case "$PKG_MANAGER" in
        apt)
            apt-get update -qq
            apt-get install -y -qq "$TIMESYNCD_PKG" 2>&1 | tail -3
            ;;
        dnf|yum)
            $PKG_MANAGER install -y "$TIMESYNCD_PKG" 2>&1 | tail -3
            ;;
        apk)
            apk add --no-cache "$TIMESYNCD_PKG" 2>&1 | tail -3
            ;;
    esac
fi

# --- Step 2: Enable and start NTP ---
info "Enabling NTP synchronization..."
case "$PKG_MANAGER" in
    apk)
        rc-update add chrond default 2>/dev/null || true
        rc-service chrond restart 2>/dev/null || true
        chronyd -q || warn "chronyd sync failed"
        ;;
    *)
        # systemd-based
        systemctl enable --now systemd-timesyncd 2>/dev/null || true

        # If timesyncd didn't start, try chrony
        if ! systemctl is-active systemd-timesyncd &>/dev/null; then
            warn "systemd-timesyncd not available, trying chrony..."
            $PKG_MANAGER install -y chrony 2>/dev/null || true
            systemctl enable --now chronyd 2>/dev/null || true
        fi

        # Force an immediate sync
        if systemctl is-active systemd-timesyncd &>/dev/null; then
            systemctl restart systemd-timesyncd
            # Wait for sync
            info "Waiting for NTP sync..."
            for i in $(seq 1 15); do
                if timedatectl status 2>/dev/null | grep -q "synchronized: yes"; then
                    break
                fi
                sleep 2
            done
        elif systemctl is-active chronyd &>/dev/null; then
            chronyd -q
        fi
        ;;
esac

# --- Step 3: Verify ---
echo ""
echo "=== Post-fix time state ==="
timedatectl status 2>/dev/null
echo ""
echo "Current UTC time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

# Final check
ntp_ok=false
if timedatectl status 2>/dev/null | grep -qi 'synchronized: yes'; then
    ntp_ok=true
fi

if $ntp_ok; then
    info "✅ System clock synchronized successfully."
    info "Next step: run 'tools/verify_bitget_api.sh' to verify API access."
    exit 0
else
    warn "⚠️  NTP may not be fully synchronized yet."
    warn "Check: timedatectl status"
    warn "Try manual sync: timedatectl set-ntp true"
    exit 1
fi
