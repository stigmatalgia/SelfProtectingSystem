#!/usr/bin/env python3
# Script per l'inoltro veloce degli alert dai log degli IDS al validatore blockchain.
# Utilizza una coda in memoria e worker multipli per gestire alti volumi di traffico.
import sys
import os
import io
import time
import json
import threading
import queue
import http.client
from datetime import datetime

# Configurazione ambiente UTF-8
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['LC_ALL'] = 'C.UTF-8'
os.environ['LANG'] = 'C.UTF-8'

# Protezione contro errori di codifica in ambienti Kathara
if sys.stdout.encoding is None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding is None:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

if len(sys.argv) < 4:
    print("Usage: alert_forwarder.py <log_file> <validator_ip> <ids_name>")
    sys.exit(1)

LOG_FILE = sys.argv[1]
VALIDATOR_IP = sys.argv[2]
IDS_NAME = sys.argv[3]

ALERTS = ["SQL_INJECTION", "XSS_ATTACK", "PATH_TRAVERSAL", "COMMAND_INJECTION"]

# Coda in RAM per gli alert in uscita
alert_queue = queue.Queue(maxsize=200000)

def forwarder_worker(worker_id):
    """Operaio che legge dalla coda e invia all'API usando una connessione persistente."""
    conn = None
    
    def connect():
        nonlocal conn
        if conn:
            conn.close()
        # Creiamo una connessione HTTP. timeout corto per non bloccarci
        conn = http.client.HTTPConnection(VALIDATOR_IP, 3000, timeout=2)

    connect()
    
    while True:
        # Prende l'alert dalla coda (si blocca se è vuota)
        payload = alert_queue.get()
        data = json.dumps(payload).encode('utf-8')
        
        success = False
        retries = 3
        while not success and retries > 0:
            try:
                # Usiamo Keep-Alive per non chiudere il socket
                headers = {'Content-Type': 'application/json', 'Connection': 'keep-alive'}
                conn.request("POST", "/alert", body=data, headers=headers)
                response = conn.getresponse()
                response.read() # Leggiamo la risposta per liberare il socket
                
                if response.status in [200, 202]:
                    success = True
                else:
                    retries -= 1
            except Exception:
                # Se l'API chiude la connessione, ci riconnettiamo
                connect()
                retries -= 1
                time.sleep(0.1)
                
        alert_queue.task_done()

def follow(file_path):
    while True:
        line = file_path.readline()
        if not line:
            time.sleep(0.05)
            continue
        yield line

def main():
    sys.stdout.write(f"[INIT] Fast Forwarder started: {IDS_NAME} -> {VALIDATOR_IP}:3000\n")
    sys.stdout.flush()

    while not os.path.exists(LOG_FILE):
        time.sleep(1)

    # Avviamo 50 worker persistenti
    for i in range(50):
        t = threading.Thread(target=forwarder_worker, args=(i,), daemon=True)
        t.start()

    with open(LOG_FILE, "r") as f:
        f.seek(0, os.SEEK_END)
        for line in follow(f):
            line_lower = line.lower()
            
            # Recupero (Negative alert)
            if "negative alert: " in line_lower:
                try:
                    alert_type = line.split("NEGATIVE ALERT: ")[1].strip()
                    payload = {
                        "ids": IDS_NAME, "message": "Recovery", 
                        "type": alert_type, "value": 0, "timestamp": datetime.now().isoformat()
                    }
                    alert_queue.put(payload)
                except: pass
                continue

            # Attacchi
            for alert_type in ALERTS:
                if alert_type.lower() in line_lower or alert_type.replace("_", " ").lower() in line_lower:
                    payload = {
                        "ids": IDS_NAME, "message": line.strip(), 
                        "type": alert_type, "value": 1, "timestamp": datetime.now().isoformat()
                    }
                    alert_queue.put(payload)
                    break

if __name__ == "__main__":
    main()