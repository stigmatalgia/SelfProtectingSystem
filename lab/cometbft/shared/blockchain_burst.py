import sys
import time
import random
import urllib.request
import json
from concurrent.futures import ThreadPoolExecutor

def send_request(idx, target_ip):
    url = f"http://{target_ip}:3000/stress"
    # Send empty or generic data
    data = json.dumps({"type": "SQL_INJECTION", "value": 1}).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=data, method="POST", headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            pass 
    except Exception:
        pass

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
    max_workers = min(200, n)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for i in range(n):
            target = ips[i % len(ips)]
            executor.submit(send_request, i, target)
            
    duration = time.time() - start_time
    print(f"--- Blockchain Direct Burst END (Duration: {duration:.2f}s) ---")

if __name__ == "__main__":
    main()
