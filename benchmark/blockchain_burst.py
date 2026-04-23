import sys
import time
import random
import urllib.request
import json
from concurrent.futures import ThreadPoolExecutor

import threading
import http.client

# Thread-local for persistent connections
thread_local = threading.local()

def get_connection(target_ip):
    # We use a dict in thread_local to handle multiple target IPs per thread
    if not hasattr(thread_local, "conns"):
        thread_local.conns = {}
    if target_ip not in thread_local.conns:
        thread_local.conns[target_ip] = http.client.HTTPConnection(target_ip, 3000, timeout=10)
    return thread_local.conns[target_ip]

def send_request(idx, target_ip):
    """Sends a stress request reusing a persistent connection."""
    conn = get_connection(target_ip)
    payload = json.dumps({"type": "SQL_INJECTION", "value": 1}).encode('utf-8')
    
    try:
        headers = {'Content-Type': 'application/json', 'Connection': 'keep-alive'}
        conn.request("POST", "/stress", body=payload, headers=headers)
        res = conn.getresponse()
        res.read()
    except Exception:
        # Re-init connection on failure
        try:
            conn.close()
        except:
            pass
        thread_local.conns[target_ip] = http.client.HTTPConnection(target_ip, 3000, timeout=10)

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 blockchain_burst.py <N> [node_ips...]")
        sys.exit(1)
    
    try:
        n = int(sys.argv[1])
    except ValueError:
        print("Error: N must be an integer")
        sys.exit(1)
        
    ips = sys.argv[2:]
    if not ips:
        print("Error: No target IPs provided")
        sys.exit(1)
    
    print(f"--- Blockchain Direct Burst START (N={n}) ---")
    print(f"Targets: {ips}")
    
    start_time = time.time()
    # High concurrency for burst density
    max_workers = min(330, n)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for i in range(n):
            target = ips[i % len(ips)]
            executor.submit(send_request, i, target)
            
    duration = time.time() - start_time
    print(f"--- Blockchain Direct Burst END (Duration: {duration:.2f}s) ---")

if __name__ == "__main__":
    main()
