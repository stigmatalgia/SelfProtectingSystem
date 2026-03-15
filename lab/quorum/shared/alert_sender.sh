# Script in esecuzione dalle macchine con IDS come descritto nello startup
# Monitora i log del processo IDS, quando viene appesa una nuova riga di log
# invia alla blockchain_api del nodo membro associato una richiesta per far
# cambiare stato alla blockchain e rispondere all'attacco.
set -o nounset

#Costanti del caso
LOG_FILE="${1:?Errore: specificare log file}"
VALIDATOR_IP="${2:?Errore: specificare IP validatore}"
IDS_NAME="${3:?Errore: specificare nome IDS}"
API_URL="http://${VALIDATOR_IP}:3000/alert"

ALERTS=(
    "SQL_INJECTION"
    "XSS_ATTACK"
    "PATH_TRAVERSAL"
    "COMMAND_INJECTION"
)

CURRENT_STATE="SAFE_ENVIRONMENT"

FEEDBACK_LOG="/var/log/ids_feedback.log"
touch "$FEEDBACK_LOG"

# Funzione per inviare il payload via curl
send_payload() {
    local p_ids="$1"
    local p_message="$2"
    local p_type="$3"
    local p_val="${4:-1}"
    local p_timestamp=$(date -Iseconds)
    local p_payload="{\"ids\":\"$p_ids\",\"message\":\"$p_message\",\"type\":\"$p_type\",\"value\":$p_val,\"timestamp\":\"$p_timestamp\"}"
    
    echo "Inviando alert: $p_type (value: $p_val)"
    curl -s -m 2 -X POST -H "Content-Type: application/json" -d "$p_payload" "$API_URL" >/dev/null || true
}

# Il flag -F segue il file anche se viene ruotato o troncato
tail -n0 -q -F "$LOG_FILE" "$FEEDBACK_LOG" 2>/dev/null | while read -r line; do
    
    if [[ "$line" == "NEGATIVE ALERT: "* ]]; then
        type="${line#NEGATIVE ALERT: }"
        send_payload "$IDS_NAME" "Recovery completed" "$type" 0
        continue
    fi

    line_lower="${line,,}"
    for type in "${ALERTS[@]}"; do
        type_space="${type//_/ }"
        if [[ "$line_lower" == *"${type,,}"* ]] || [[ "$line_lower" == *"${type_space,,}"* ]]; then
            send_payload "$IDS_NAME" "${line//\"/\\\"}" "$type" 1
            break
        fi
    done
done