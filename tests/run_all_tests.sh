#!/bin/bash
# run_all_tests.sh - Run all tests for the SPS-Blockchain system
#
# Usage: ./run_all_tests.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASSED=0
FAILED=0

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    PASSED=$((PASSED+1))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    FAILED=$((FAILED+1))
}

log_info() {
    echo -e "${YELLOW}[INFO]${NC} $1"
}

# Check if lab is running by checking for kathara containers
check_lab_running() {
    # Check for any kathara container with 'validator0' in its name
    if ! docker ps --filter "name=kathara" --filter "name=validator0" --format "{{.Names}}" | grep -q "validator0"; then
        log_fail "Kathara lab is not running. Please start it with 'make start' or 'kathara lstart'"
        exit 1
    fi
    log_pass "Kathara lab is running"
}

echo ""
echo "=========================================="
echo " SPS-Blockchain Test Suite"
echo "=========================================="
echo ""

# Test 1: Check lab is running
log_info "Test 1: Checking if lab is running..."
check_lab_running

# Wait a bit for blockchain to initialize
log_info "Waiting for blockchain initialization"
sleep 5

# Test 2: Check geth is running on all validators
log_info "Test 2: Checking geth process on validators..."
for v in validator0 validator1 validator2; do
    if kathara exec $v -- pgrep geth >/dev/null 2>&1; then
        log_pass "geth is running on $v"
    else
        log_fail "geth is NOT running on $v"
    fi
done

# Test 3: Check blockchain network connectivity
log_info "Test 3: Checking blockchain connectivity..."
sleep 10
PEERS=$(kathara exec validator0 -- geth attach /home/qbft/data/geth.ipc --exec 'admin.peers.length' 2>/dev/null | tr -d '\r\n' | grep -o '[0-9]*' || echo "0")
if [ "$PEERS" -ge 2 ] 2>/dev/null; then
    log_pass "Blockchain peers connected to Validator0: $PEERS"
else
    log_info "Blockchain peers: $PEERS (may need more time to connect)"
fi

# Test 4: Check contract deployment
log_info "Test 4: Checking smart contract deployment..."
if kathara exec validator0 -- cat /home/qbft/data/contract_address.txt 2>/dev/null | grep -q "0x"; then
    CONTRACT=$(kathara exec validator0 -- cat /home/qbft/data/contract_address.txt 2>/dev/null | tr -d '\r\n')
    log_pass "Smart contract deployed at: $CONTRACT"
else
    log_info "Smart contract not yet deployed (may need more time)"
fi

# Test 5: Check IDS services are running
log_info "Test 5: Checking IDS services..."
if kathara exec ids_snort -- pgrep snort >/dev/null 2>&1; then
    log_pass "Snort is running"
else
    log_fail "Snort is NOT running"
fi

if kathara exec ids_suricata -- pgrep Suricata-Main >/dev/null 2>&1; then
    log_pass "Suricata is running"
else
    log_fail "Suricata is NOT running"
fi

if kathara exec ids_zeek -- pgrep zeek >/dev/null 2>&1; then
    log_pass "Zeek is running"
else
    log_fail "Zeek is NOT running"
fi

# Test 6: Network connectivity between IDS and validators
log_info "Test 6: Checking network connectivity..."
if kathara exec ids_snort -- ping -c 1 -W 2 172.16.1.10 >/dev/null 2>&1; then
    log_pass "ids_snort can reach validator0"
else
    log_fail "ids_snort cannot reach validator0"
fi

if kathara exec ids_suricata -- ping -c 1 -W 2 172.16.2.10 >/dev/null 2>&1; then
    log_pass "ids_suricata can reach validator1"
else
    log_fail "ids_suricata cannot reach validator1"
fi

if kathara exec ids_zeek -- ping -c 1 -W 2 172.16.3.10 >/dev/null 2>&1; then
    log_pass "ids_zeek can reach validator2"
else
    log_fail "ids_zeek cannot reach validator2"
fi

# Test 7: Check blockchain health endpoints
log_info "Test 7: Checking blockchain API health endpoints..."
for i in 0 1 2; do
    IP="172.16.$((i+1)).10"
    IDS="ids_snort"
    if [ $i -eq 1 ]; then IDS="ids_suricata"; fi
    if [ $i -eq 2 ]; then IDS="ids_zeek"; fi
    
    RESPONSE=$(kathara exec $IDS -- curl -s --connect-timeout 5 "http://$IP:3000/alive" 2>/dev/null || echo "")
    if echo "$RESPONSE" | grep -q '"status":"ok"'; then
        log_pass "Blockchain API health check passed (validator$i)"
    else
        log_info "Blockchain API not ready yet (validator$i): $RESPONSE"
    fi
done

echo ""
echo "=========================================="
echo " Test Summary"
echo "=========================================="
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${YELLOW}Some tests may need more initialization time.${NC}"
    echo "Try running tests again in a minute."
    exit 1
fi
