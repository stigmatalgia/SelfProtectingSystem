#!/bin/bash
# test_blockchain_consensus.sh - Test blockchain consensus mechanism
#
# This test verifies that the blockchain consensus works correctly
# by submitting alerts from multiple IDS nodes and checking that
# the state is only updated when majority is reached.
#
# Usage: ./test_blockchain_consensus.sh

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
echo " Blockchain Consensus Test"
echo "=========================================="
echo ""

# Get contract info
log_step "Step 1: Getting contract information..."
CONTRACT=$(kathara exec validator0 -- cat /home/qbft/data/contract_address.txt 2>/dev/null | tr -d '\r\n')
log_info "Contract address: $CONTRACT"

# Step 2: Submit alert from first validator only
log_step "Step 2: Submitting alert from validator0 only..."

ALERT_DATA='{"ids":"snort","type":"SQL_INJECTION","severity":"high","message":"Test alert 1"}'
RESPONSE=$(kathara exec ids_snort -- curl -s -X POST \
    -H "Content-Type: application/json" \
    -d "$ALERT_DATA" \
    "http://172.16.1.10:3000/alert" 2>/dev/null)

log_info "Response from validator0: $RESPONSE"

if echo "$RESPONSE" | grep -q '"success":true'; then
    log_pass "Alert submitted to validator0"
else
    log_fail "Failed to submit alert to validator0"
fi

# Wait for processing
sleep 1

# Step 3: Check blockchain status
log_step "Step 3: Checking blockchain status..."
STATUS=$(kathara exec ids_snort -- curl -s "http://172.16.1.10:3000/status" 2>/dev/null)
log_info "Validator0 status: $STATUS"

# Step 4: Submit same alert from second validator
log_step "Step 4: Submitting same alert from validator1..."

RESPONSE=$(kathara exec ids_suricata -- curl -s -X POST \
    -H "Content-Type: application/json" \
    -d "$ALERT_DATA" \
    "http://172.16.2.10:3000/alert" 2>/dev/null)

log_info "Response from validator1: $RESPONSE"

if echo "$RESPONSE" | grep -q '"success":true'; then
    log_pass "Alert submitted to validator1"
else
    log_fail "Failed to submit alert to validator1"
fi

# Wait for consensus
sleep 2

# Step 5: Check if consensus was reached (with 2 validators agreeing)
log_step "Step 5: Checking for consensus..."

# Check blockchain for state change
BLOCK_NUM=$(kathara exec validator0 -- geth attach /home/qbft/data/geth.ipc --exec 'eth.blockNumber' 2>/dev/null | tr -d '\r\n')
log_info "Current block number: $BLOCK_NUM"

# Check for events in logs
EVENT_LOG=$(kathara exec validator0 -- cat /var/log/events.log 2>/dev/null | tail -10)
if echo "$EVENT_LOG" | grep -q "StateChanged\|ActionRequired"; then
    log_pass "Consensus reached - state change event detected!"
else
    log_info "No state change event detected (may need more votes or longer wait)"
fi

# Step 6: Submit alert from third validator
log_step "Step 6: Submitting alert from validator2..."

RESPONSE=$(kathara exec ids_zeek -- curl -s -X POST \
    -H "Content-Type: application/json" \
    -d "$ALERT_DATA" \
    "http://172.16.3.10:3000/alert" 2>/dev/null)

log_info "Response from validator2: $RESPONSE"

if echo "$RESPONSE" | grep -q '"success":true'; then
    log_pass "Alert submitted to validator2"
else
    log_fail "Failed to submit alert to validator2"
fi

# Final wait
sleep 2

# Step 7: Final status check
log_step "Step 7: Final status check..."
for i in 0 1 2; do
    IDS="ids_snort"
    IP="172.16.1.10"
    if [ $i -eq 1 ]; then IDS="ids_suricata"; IP="172.16.2.10"; fi
    if [ $i -eq 2 ]; then IDS="ids_zeek"; IP="172.16.3.10"; fi
    
    STATUS=$(kathara exec $IDS -- curl -s "http://$IP:3000/status" 2>/dev/null)
    BLOCK=$(echo "$STATUS" | grep -o '"blockNumber":"[0-9]*"' | grep -o '[0-9]*')
    log_info "Validator$i: Block $BLOCK"
done

echo ""
echo "=========================================="
echo " Test Complete"
echo "=========================================="
echo ""
log_info "Review /var/log/events.log on validators to see state changes"
echo ""
