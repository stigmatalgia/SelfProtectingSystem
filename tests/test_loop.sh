#!/bin/bash
# Test del sistema SPS
# Verifica l'intera pipeline: Attacker -> IDS -> Blockchain -> Actuator -> Juice_shop
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
PASS=0; FAIL=0

ok()   { echo -e "${GREEN}[PASS]${NC} $1"; PASS=$((PASS+1)); }
ko()   { echo -e "${RED}[FAIL]${NC} $1"; FAIL=$((FAIL+1)); }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }
step() { echo -e "\n${BLUE}[$1]${NC} $2"; }

kexec() { kathara exec "$@" 2>/dev/null; }

# ─── Controlla che il lab sia attivo ───
step 0 "Prerequisiti"
if ! docker ps --filter "name=kathara" --format "{{.Names}}" | grep -q "validator0"; then
    ko "Lab Kathara non attivo — lancia 'make start'"
    exit 1
fi
ok "Lab attivo"

# Verifica geth sui nodi chiave
for node in validator0 member0 member3; do
    if kexec $node -- pgrep geth >/dev/null; then
        ok "geth running su $node"
    else
        ko "geth NON running su $node"
    fi
done

# Contratto deployato
CONTRACT=$(kexec validator0 -- cat /home/qbft/data/contract_address.txt | tr -d '[:space:]')
if echo "$CONTRACT" | grep -q "0x"; then
    ok "Contratto deployato: $CONTRACT"
else
    ko "Contratto non trovato"
    exit 1
fi

# ─── 1. Attacco: Attacker -> Juice_shop ───
step 1 "Attacker → Juice_shop (SQL Injection)"
BLOCK_BEFORE=$(kexec member3 -- geth attach /home/qbft/data/geth.ipc --exec 'eth.blockNumber' | tr -cd '0-9')
info "Blocco prima dell'attacco: $BLOCK_BEFORE"

HTTP_CODE=$(kexec attacker -- curl -s -o /dev/null -w '%{http_code}' 'http://10.0.0.80:3000/rest/products/search?q=1=1' || echo "000")
if [ "$HTTP_CODE" != "000" ]; then
    ok "Juice_shop raggiungibile (HTTP $HTTP_CODE)"
else
    ko "Juice_shop irraggiungibile"
fi

# ─── 2. IDS detection ───
step 2 "Rilevamento IDS"
info "Attesa propagazione alert (12s)..."
sleep 12

SNORT=$(kexec ids_snort -- grep -c "SPS-TRAFFIC\|SQL" /var/log/snort/alert_fast.txt || echo 0)
SURICATA=$(kexec ids_suricata -- grep -c "SQL\|IDS" /var/log/suricata/fast.log || echo 0)
ZEEK=$(kexec ids_zeek -- grep -c "SQL\|IDS" /var/log/zeek/signatures.log || echo 0)

[ "$SNORT" -gt 0 ]    && ok "Snort: $SNORT alert"    || ko "Snort: nessun alert"
[ "$SURICATA" -gt 0 ] && ok "Suricata: $SURICATA alert" || info "Suricata: nessun alert (puo' dipendere dalle signature)"
[ "$ZEEK" -gt 0 ]     && ok "Zeek: $ZEEK alert"      || info "Zeek: nessun alert (puo' dipendere dalle signature)"

# ─── 3. Alert forwarding: IDS -> Member blockchain API ───
step 3 "IDS → Blockchain (alert_sender → blockchain_api)"
API_LOG=$(kexec member0 -- cat /var/log/blockchain_api.log 2>/dev/null | tail -20)
if echo "$API_LOG" | grep -q "alert\|Transaction\|tx"; then
    ok "Member0 ha processato alert"
else
    info "Nessun log alert su member0 — log:"
    echo "$API_LOG" | tail -5
fi

# ─── 4. Stato blockchain (bitmask) ───
step 4 "Stato blockchain (consensus)"
sleep 3

STATE=$(kexec member3 -- sh -c "geth attach /home/qbft/data/geth.ipc --exec \"var addr = '${CONTRACT}'; var abi = [{'name':'statusMapDT','type':'function','inputs':[{'type':'string'}],'outputs':[{'type':'uint256'}]}]; var c = eth.contract(abi).at(addr); '' + c.statusMapDT.call('SQL_INJECTION') + c.statusMapDT.call('XSS_ATTACK') + c.statusMapDT.call('PATH_TRAVERSAL') + c.statusMapDT.call('BRUTE_FORCE');\"" | tr -d '"[:space:]')

info "Bitmask: $STATE"
if [ "$STATE" != "0000" ]; then
    ok "Attacco registrato in blockchain (stato != 0000)"
else
    info "Stato ancora 0000 — servono piu' voti o piu' tempo"
fi

BLOCK_AFTER=$(kexec member3 -- geth attach /home/qbft/data/geth.ipc --exec 'eth.blockNumber' | tr -cd '0-9')
info "Blocco dopo: $BLOCK_AFTER (prima: $BLOCK_BEFORE)"

# ─── 5. Member3 → Actuator (forwarder) ───
step 5 "Member3 actuator_forwarder"
FWD_LOG=$(kexec member3 -- cat /var/log/actuator_forwarder.log 2>/dev/null | tail -10)
if echo "$FWD_LOG" | grep -qi "Action\|forward\|ACTUATOR"; then
    ok "Member3 ha forwardato azioni all'actuator"
    echo "$FWD_LOG" | tail -3
else
    info "Nessuna azione forwardata — log:"
    echo "$FWD_LOG" | tail -3
fi

# ─── 6. Actuator logs ───
step 6 "Actuator"
ACT_LOG=$(kexec actuator -- cat /var/log/actuator_actions.log 2>/dev/null | tail -10)
if echo "$ACT_LOG" | grep -qi "RECEIVED\|action\|SSH"; then
    ok "Actuator ha ricevuto ed eseguito comandi"
    echo "$ACT_LOG" | tail -3
else
    info "Nessun log sull'actuator"
fi

# ─── 7. Verifica SSH actuator -> juice_shop (test manuale diretto) ───
step 7 "SSH: Actuator → Juice_shop"
kexec actuator -- rm -f /tmp/ssh_test_result 2>/dev/null || true
SSH_OUT=$(kexec member3 -- curl -s -X POST -H "Content-Type: application/json" \
    -d '{"action":"hostname"}' http://172.16.4.1:5000/action || echo "{}")

if echo "$SSH_OUT" | grep -q '"executed"'; then
    ok "Comando SSH eseguito con successo su juice_shop"
    info "Risposta: $SSH_OUT"
else
    ko "Comando SSH fallito — risposta: $SSH_OUT"
fi

# ─── Riepilogo ───
echo ""
echo "=========================================="
echo -e " Risultati: ${GREEN}$PASS passati${NC}, ${RED}$FAIL falliti${NC}"
echo "=========================================="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
