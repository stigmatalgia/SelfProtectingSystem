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
    "BRUTE_FORCE"
)

CURRENT_STATE="SAFE_ENVIRONMENT"

# Funzione per inviare il payload via curl
send_payload() {
    local p_ids="$1"
    local p_message="$2"
    local p_type="$3"
    local p_timestamp=$(date -Iseconds)
    local p_payload="{\"ids\":\"$p_ids\",\"message\":\"$p_message\",\"type\":\"$p_type\",\"timestamp\":\"$p_timestamp\"}"
    
    echo "Inviando alert: $p_type"
    curl -s -m 2 -X POST -H "Content-Type: application/json" -d "$p_payload" "$API_URL" >/dev/null || true
}

# Il flag -F segue il file anche se viene ruotato o troncato
tail -n0 -F "$LOG_FILE" 2>/dev/null | while read -r line; do
    
    # Se arriviamo qui, abbiamo ricevuto una riga vera dal log
    line_lower="${line,,}"
    
    for type in "${ALERTS[@]}"; do
        type_space="${type//_/ }"
        if [[ "$line_lower" == *"${type,,}"* ]] || [[ "$line_lower" == *"${type_space,,}"* ]]; then
            
            CURRENT_STATE="$type"
            
            # TODO: sanificazione input tramite libreria
            safe_line="${line//\"/\\\"}"
            
            # Invia l'alert dell'attacco
            send_payload "$IDS_NAME" "$safe_line" "$type"
            
            # Invia IMMEDIATAMENTE il ritorno a safe
            # Assumendo che la risposta mitigatrice sia istantanea
            CURRENT_STATE="SAFE_ENVIRONMENT"
            send_payload "$IDS_NAME" "Ripristino" "SAFE_ENVIRONMENT"
            
            break
        fi
    done
done