# Script per simulare una raffica di attacchi (SQLi, XSS, ecc.) verso il Juice Shop.
# Utilizzato per testare la capacità di rilevamento e risposta del sistema sotto carico.

import random
import sys
import subprocess
import time
import urllib.request
import argparse
from concurrent.futures import ThreadPoolExecutor

# Target configuration
TARGET_HOST = "10.0.0.80"

TARGET_PORT = 3000

# Comandi curl casuali per simulare diversi tipi di attacco
CURL_COMMANDS = [
    f"curl -s -o /dev/null -w '%{{http_code}}' 'http://{TARGET_HOST}:{TARGET_PORT}/rest/products/search?q=1=1'",
    f"curl -s -o /dev/null -w '%{{http_code}}' 'http://{TARGET_HOST}:{TARGET_PORT}/rest/products/search?q=<script>alert(1)</script>'",
    f"curl -s -o /dev/null -w '%{{http_code}}' 'http://{TARGET_HOST}:{TARGET_PORT}/rest/products/search?q=cat+/etc/passwd'",
    f"curl -s -o /dev/null -w '%{{http_code}}' --path-as-is 'http://{TARGET_HOST}:{TARGET_PORT}/public/images/../../../../'"
]

def send_request_pattern(idx, pattern):
    """Invia una singola richiesta seguendo il pattern scelto."""
    if pattern == "cycle":
        cmd = CURL_COMMANDS[idx % len(CURL_COMMANDS)]
    else:
        cmd = random.choice(CURL_COMMANDS)

    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        status = res.stdout.strip()
        print(f"[Attack {idx}] HTTP {status}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"[Attack {idx}] TIMEOUT", flush=True)
    except Exception as e:
        print(f"[Attack {idx}] EXCEPTION: {str(e)}", flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("N", type=int, help="Numero totale di richieste di attacco")
    parser.add_argument(
        "--pattern",
        choices=["random", "cycle"],
        default="random",
        help="Pattern di invio attacchi: random (default) o cycle"
    )
    args = parser.parse_args()

    n = args.N
    
    print(f"--- Attacker Diagnostic Burst START (N={n}) ---", flush=True)
    print(f"Target: {TARGET_HOST}:{TARGET_PORT}", flush=True)
    
    # Controllo preliminare della connettività
    try:
        req = urllib.request.Request(f"http://{TARGET_HOST}:{TARGET_PORT}", method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            print(f"Pre-burst connectivity test: HTTP {response.getcode()}", flush=True)
    except Exception as e:
        print(f"Pre-burst connectivity test FAILED: {str(e)}", flush=True)

    start_time = time.time()
    
    # Imposta max_workers a 200 per una maggiore densità di traffico
    max_workers = min(200, n)
    print(f"Firing {n} attacks using {max_workers} threads...", flush=True)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for i in range(n):
            executor.submit(send_request_pattern, i, args.pattern)
            
    end_time = time.time()
    print(f"--- Attacker Diagnostic Burst END (Duration: {end_time - start_time:.2f}s) ---", flush=True)
    sys.stdout.flush()

if __name__ == "__main__":
    main()