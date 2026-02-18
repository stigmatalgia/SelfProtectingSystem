#!/bin/bash
# Configura una mappa "TIPO_ATTACCO"="GRAVITA DELL'ATTACCO",
# monitora i log e se trova il tipo di attacco invia
# un allerta con la gravità corrispondente.
set -o nounset

LOG_FILE="${1:?Errore: specificare log file}"
VALIDATOR_IP="${2:?Errore: specificare IP validatore}"
IDS_NAME="${3:?Errore: specificare nome IDS}"
API_URL="http://${VALIDATOR_IP}:3000/alert"

declare -A ATTACKS=(
    ["SQL_INJECTION"]="high"
    ["XSS_ATTACK"]="medium"
    ["PATH_TRAVERSAL"]="high"
    ["BRUTE_FORCE"]="low"
)

tail -n0 -F "$LOG_FILE" 2>/dev/null | while read -r line; do
    line_lower="${line,,}"
    
    for type in "${!ATTACKS[@]}"; do
        if [[ "$line_lower" == *"${type,,}"* ]]; then
            severity="${ATTACKS[$type]}"
            #TODO: il metodo di sanificare l'input a questo modo e' in realta' unsafe, da aggiungere la sanificazione da libreria.
            safe_line="${line//\"/\\\"}"
            timestamp=$(date -Iseconds)
            
            payload="{\"ids\":\"$IDS_NAME\",\"message\":\"$safe_line\",\"severity\":\"$severity\",\"type\":\"$type\",\"timestamp\":\"$timestamp\"}"

            curl -s -m 2 -X POST -H "Content-Type: application/json" -d "$payload" "$API_URL" >/dev/null || true
        fi
    done
done