#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${YELLOW}[INFO]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
}

echo ""
echo "=========================================="
echo " End-to-End Attack Flow Test"
echo "=========================================="
echo ""

# Step 1: Get initial blockchain state
log_step "Step 1: Getting initial blockchain state..."
INITIAL_BLOCK=$(kathara exec validator0 -- geth attach /home/qbft/data/geth.ipc --exec 'eth.blockNumber' 2>/dev/null | tr -d '\r\n')
log_info "Initial block number: $INITIAL_BLOCK"

# Step 2: Get contract address
log_step "Step 2: Getting contract address..."
CONTRACT=$(kathara exec validator0 -- cat /home/qbft/data/contract_address.txt 2>/dev/null | tr -d '\r\n')
log_info "Contract address: $CONTRACT"

# Step 3: Simulate SQL injection attack
log_step "Step 3: Simulating SQL injection attack..."
log_info "Sending malicious request from attacker to juice_shop..."

# The attacker sends a request with SQL injection pattern
ATTACK_RESPONSE=$(kathara exec attacker -- curl "http://10.0.0.80:3000/rest/products/search?q=1=1" || true)

log_info "Attack request sent"

# Wait for alert processing
log_info "Waiting for alert processing"
sleep 1

# Step 4: Check if IDS detected the attack2
log_step "Step 4: Checking IDS detection..."

# Check Snort alerts
SNORT_ALERTS=$(kathara exec ids_snort -- cat /var/log/snort/alert_fast.txt 2>/dev/null | grep -c "SQL Injection\|SPS-TRAFFIC" || true)
if [ "$SNORT_ALERTS" -gt 0 ]; then
    log_pass "Snort detected $SNORT_ALERTS alert(s)"
else
    log_info "Snort did not detect alerts"
fi

# Check Suricata alerts
SURICATA_ALERTS=$(kathara exec ids_suricata -- cat /var/log/suricata/fast.log 2>/dev/null | grep -c "SQL Injection\|IDS-SURICATA" || true)
if [ "$SURICATA_ALERTS" -gt 0 ]; then
    log_pass "Suricata detected $SURICATA_ALERTS alert(s)"
else
    log_info "Suricata did not detect alerts"
fi

# Check Zeek alerts
ZEEK_ALERTS=$(kathara exec ids_zeek -- cat /var/log/zeek/signatures.log 2>/dev/null | grep -c "SQL Injection\|IDS-ZEEK" || true)
if [ "$ZEEK_ALERTS" -gt 0 ]; then
    log_pass "Zeek detected $ZEEK_ALERTS alert(s)"
else
    log_info "Zeek did not detect alerts"
fi

# Step 5: Check blockchain state change
log_step "Step 5: Checking blockchain state..."
FINAL_BLOCK=$(kathara exec validator0 -- geth attach /home/qbft/data/geth.ipc --exec 'eth.blockNumber' 2>/dev/null | tr -d '\r\n')
log_info "Final block number: $FINAL_BLOCK"

if [ "$FINAL_BLOCK" -gt "$INITIAL_BLOCK" ]; then
    log_pass "Blockchain progressed from block $INITIAL_BLOCK to $FINAL_BLOCK"
else
    log_info "No new blocks created (this is normal if no alerts were forwarded)"
fi

# Step 6: Check blockchain API logs
log_step "Step 6: Checking blockchain API activity..."
API_LOGS=$(kathara exec validator0 -- cat /var/log/blockchain_api.log 2>/dev/null | tail -20)
if echo "$API_LOGS" | grep -q "Received alert\|Transaction submitted"; then
    log_pass "Blockchain API has processed requests"
else
    log_info "No API requests logged"
    log_info "Last logs:"
    echo "$API_LOGS"
fi

# Step 7: Summary
echo ""
echo "=========================================="
echo " Test Summary"
echo "=========================================="
echo ""
TOTAL_ALERTS=$((SNORT_ALERTS + SURICATA_ALERTS + ZEEK_ALERTS))
log_info "Total IDS alerts detected: $TOTAL_ALERTS"
log_info "Blocks processed: $((FINAL_BLOCK - INITIAL_BLOCK))"
echo ""

if [ "$TOTAL_ALERTS" -gt 0 ]; then
    log_pass "Attack detection flow is working!"
else
    log_info "No alerts detected. Possible reasons:"
    log_info "  - Traffic mirroring not configured properly"
    log_info "  - IDS rules need tuning"
    log_info "  - Attack request didn't trigger detection rules"
fi

echo ""
echo "To manually test, run:"
echo "  kathara exec attacker -- curl 'http://10.0.0.80:3000/rest/products/search?q=1=1'"
echo "  kathara exec ids_snort -- tail -f /var/log/snort/alert_fast.txt"
echo ""
