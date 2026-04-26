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
DISABLE_NEGATIVE_MARKER = "/shared/disable_negative_alerts"

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
        batch = []
        try:
            batch.append(alert_queue.get(timeout=1.0))
            while len(batch) < 100:
                batch.append(alert_queue.get_nowait())
        except queue.Empty:
            pass

        if not batch:
            continue

        data = json.dumps(batch).encode('utf-8')
        
        success = False
        retries = 3
        while not success and retries > 0:
            try:
                # Usiamo Keep-Alive per non chiudere il socket
                headers = {'Content-Type': 'application/json', 'Connection': 'keep-alive'}
                conn.request("POST", "/alert", body=data, headers=headers)
                response = conn.getresponse()
                resp_data = response.read() # Leggiamo la risposta per liberare il socket
                
                if response.status in [200, 202, 201]:
                    success = True
                    sys.stdout.write(f"[SEND] Successfully forwarded {len(batch)} alerts to {VALIDATOR_IP}\n")
                else:
                    sys.stdout.write(f"[ERROR] Failed to forward alerts: {response.status} - {resp_data.decode()}\n")
                    retries -= 1
            except Exception as e:
                sys.stdout.write(f"[ERROR] Connection error to {VALIDATOR_IP}: {e}\n")
                # Se l'API chiude la connessione, ci riconnettiamo
                connect()
                retries -= 1
                time.sleep(0.1)
        sys.stdout.flush()
                
        for _ in batch:
            alert_queue.task_done()

def follow(file_path):
    while True:
        line = file_path.readline()
        if not line:
            time.sleep(0.1)
            continue
        yield line

def main():
    sys.stdout.write(f"[INIT] Fast Forwarder started: {IDS_NAME} -> {VALIDATOR_IP}:3000\n")
    sys.stdout.flush()

    while not os.path.exists(LOG_FILE):
        time.sleep(1)

    # Avviamo 10 worker
    for i in range(10):
        t = threading.Thread(target=forwarder_worker, args=(i,), daemon=True)
        t.start()

    with open(LOG_FILE, "r") as f:
        # NON facciamo f.seek(0, os.SEEK_END) per non perdere alert iniziali
        for line in follow(f):
            line_lower = line.lower()
            
            # Attacchi
            found = False
            for alert_type in ALERTS:
                line_lower = line.lower()
                patterns = [
                    alert_type.lower(),                                 # sql_injection
                    alert_type.replace("_", " ").lower(),              # sql injection
                    alert_type.replace("_", "-").lower(),              # sql-injection
                ]
                
                if any(p in line_lower for p in patterns):
                    payload = {
                        "ids": IDS_NAME, "message": line.strip(), 
                        "type": alert_type, "value": 1, "timestamp": datetime.now().isoformat()
                    }
                    alert_queue.put(payload)
                    sys.stdout.write(f"[MATCH] Found {alert_type} in {IDS_NAME} logs\n")
                    sys.stdout.flush()
                    found = True
                    break
            
            if not found and "negative alert" in line_lower:
                 # Trattiamo i negative alert separatamente
                 for alert_type in ALERTS:
                     if alert_type.lower() in line_lower:
                         payload = {
                            "ids": IDS_NAME, "message": "Recovery", 
                            "type": alert_type, "value": 0, "timestamp": datetime.now().isoformat()
                         }
                         alert_queue.put(payload)
                         break

if __name__ == "__main__":
    main()