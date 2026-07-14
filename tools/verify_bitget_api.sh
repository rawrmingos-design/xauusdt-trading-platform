#!/usr/bin/env bash
# Verify Bitget public market data API access.
#
# Tests:
#   1. GET /api/v2/mix/market/history-candles (with valid range)
#   2. GET /api/v2/mix/market/ticker (ticker endpoint)
#   3. Check HTTP response codes and response format
#
# Usage: bash tools/verify_bitget_api.sh [--range-hours HOURS]
#
# Exit codes:
#   0 — All checks passed
#   1 — One or more API checks failed
#   2 — curl not available or network issue

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[PASS]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[FAIL]${NC} $*"; }

HOURS="${1:-4}"
BASE_URL="https://api.bitget.com"
SYMBOL="XAUUSDT_UMCBL"
GRANULARITY="15m"

# --- Pre-flight ---
if ! command -v curl &>/dev/null; then
    error "curl not found. Install: apt install -y curl"
    exit 2
fi

echo "========================================="
echo " Bitget Public API Verification"
echo "========================================="
echo ""
echo "Test machine time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "Test range: last ${HOURS} hours"
echo "⚠️  Note: If ALL endpoints return 400172, Bitget may be blocking"
echo "    this IP address (rate limiting / geo-block / bot detection)."
echo ""

# Calculate time range
# Use current time (as the machine sees it) for the test.
# Bitget returns data relative to server time, so we need valid ranges.
END_EPOCH=$(date +%s)
START_EPOCH=$((END_EPOCH - HOURS * 3600))

# Convert to millisecond timestamps (Bitget API requirement)
START_MS=$((START_EPOCH * 1000))
END_MS=$((END_EPOCH * 1000))

START_ISO=$(date -u -d "@${START_EPOCH}" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -r "${START_EPOCH}" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)
END_ISO=$(date -u -d "@${END_EPOCH}" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -r "${END_EPOCH}" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)

echo "Request range: ${START_ISO} → ${END_ISO}"
echo ""

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

# --- Test 1: History candles ---
echo "--- Test 1: Historical Candles API ---"
HISTORY_RESP=$(curl -sS -w "\n%{http_code}" \
    "${BASE_URL}/api/v2/mix/market/history-candles?symbol=${SYMBOL}&granularity=${GRANULARITY}&limit=5&startTime=${START_MS}&endTime=${END_MS}" \
    --connect-timeout 10 --max-time 15 2>&1)

HTTP_CODE=$(echo "$HISTORY_RESP" | tail -1)
BODY=$(echo "$HISTORY_RESP" | sed '$d')

if [[ "$HTTP_CODE" == "200" ]]; then
    info "HTTP ${HTTP_CODE} — candles endpoint OK"
    echo "  Response preview: ${BODY:0:200}..."
    
    # Validate JSON structure
    CODE=$(echo "$BODY" | grep -o '"code":"[0-9]*"' || echo "")
    if [[ "$CODE" == '"code":"0"' ]]; then
        info "Response code 0 (success)"
        DATA_COUNT=$(echo "$BODY" | grep -o '"data"' | wc -l)
        CANDLE_COUNT=$(echo "$BODY" | grep -o '"c"' | wc -l)
        info "Candles returned: ~${CANDLE_COUNT} rows"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        warn "API returned code: $CODE"
        echo "  Full response: ${BODY}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
elif [[ "$HTTP_CODE" == "400" ]]; then
    error "HTTP ${HTTP_CODE} — Parameter verification failed"
    echo "  Response: ${BODY}"
    echo ""
    error "⚠️  This is likely a system clock issue."
    error "  Run 'sudo bash tools/fix_time.sh' to fix NTP sync."
    FAIL_COUNT=$((FAIL_COUNT + 1))
else
    error "HTTP ${HTTP_CODE}"
    echo "  Response: ${BODY}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi
echo ""

# --- Test 2: Ticker endpoint ---
echo "--- Test 2: Ticker Market Data ---"
TICKER_RESP=$(curl -sS -w "\n%{http_code}" \
    "${BASE_URL}/api/v2/mix/market/ticker" \
    --connect-timeout 10 --max-time 15 2>&1)

HTTP_CODE=$(echo "$TICKER_RESP" | tail -1)
BODY=$(echo "$TICKER_RESP" | sed '$d')

if [[ "$HTTP_CODE" == "200" ]]; then
    info "HTTP ${HTTP_CODE} — ticker endpoint OK"
    CODE=$(echo "$BODY" | grep -o '"code":"[0-9]*"' || echo "")
    if [[ "$CODE" == '"code":"0"' ]]; then
        info "Response code 0 (success)"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        warn "API returned code: $CODE"
        echo "  Full response: ${BODY}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
elif [[ "$HTTP_CODE" == "400" ]]; then
    error "HTTP ${HTTP_CODE} — Parameter verification failed"
    echo "  Response: ${BODY}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
else
    error "HTTP ${HTTP_CODE}"
    echo "  Response: ${BODY}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi
echo ""

# --- Test 3: Different granularity (5m) ---
echo "--- Test 3: 5m Candles ---"
HISTORY_RESP=$(curl -sS -w "\n%{http_code}" \
    "${BASE_URL}/api/v2/mix/market/history-candles?symbol=${SYMBOL}&granularity=5m&limit=5&startTime=${START_MS}&endTime=${END_MS}" \
    --connect-timeout 10 --max-time 15 2>&1)

HTTP_CODE=$(echo "$HISTORY_RESP" | tail -1)
BODY=$(echo "$HISTORY_RESP" | sed '$d')

if [[ "$HTTP_CODE" == "200" ]]; then
    info "HTTP ${HTTP_CODE} — 5m candles OK"
    CODE=$(echo "$BODY" | grep -o '"code":"[0-9]*"' || echo "")
    if [[ "$CODE" == '"code":"0"' ]]; then
        info "Response code 0 (success)"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        warn "API returned code: $CODE"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
elif [[ "$HTTP_CODE" == "400" ]]; then
    error "HTTP ${HTTP_CODE} — Parameter verification failed (5m)"
    echo "  Response: ${BODY}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
else
    error "HTTP ${HTTP_CODE}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi
echo ""

# --- Summary ---
echo "========================================="
echo " Summary"
echo "========================================="
echo " Passed:  ${PASS_COUNT}/3"
echo " Failed:  ${FAIL_COUNT}/3"
echo ""

if [[ $FAIL_COUNT -eq 0 ]]; then
    info "✅ All Bitget public API tests passed!"
    info "Next step: run backfill or live collector."
    exit 0
else
    error "❌ ${FAIL_COUNT} test(s) failed."
    error "Most likely cause: system clock not synchronized."
    error "Run 'sudo bash tools/fix_time.sh', then retry."
    exit 1
fi
