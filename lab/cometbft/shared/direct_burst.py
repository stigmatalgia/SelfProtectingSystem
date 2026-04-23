import sys
import time
import json
import threading
import http.client
from concurrent.futures import ThreadPoolExecutor

thread_local = threading.local()

def get_connection(target_ip, port):
    key = f"{target_ip}:{port}"
    if not hasattr(thread_local, "conns"):
        thread_local.conns = {}
    if key not in thread_local.conns:
        thread_local.conns[key] = http.client.HTTPConnection(target_ip, port, timeout=10)
    return thread_local.conns[key]

# Cache per gli account locali di Quorum
quorum_accounts = {}

def get_quorum_account(ip):
    if ip in quorum_accounts: 
        return quorum_accounts[ip]
    try:
        conn = http.client.HTTPConnection(ip, 8545, timeout=5)
        payload = json.dumps({"jsonrpc":"2.0", "method":"eth_accounts", "params":[], "id":1})
        conn.request("POST", "/", body=payload, headers={'Content-Type':'application/json'})
        res = json.loads(conn.getresponse().read())
        acc = res.get('result', [None])[0]
        quorum_accounts[ip] = acc
        return acc
    except:
        return None

def send_comet_tx(idx, target_ip):
    """Inietta transazioni uniche direttamente nell'API Rust sps-chain per bypassare la deduplicazione"""
    conn = get_connection(target_ip, 3000)
    
    # Payload UNIVOCAMENTE ID per evitare che il batcher in Rust lo deduplichi.
    # Questo forza la catena a processare e votare ogni singola richiesta.
    payload = json.dumps({
        "type": "SQL_INJECTION",
        "value": 1
    })
    
    try:
        headers = {'Content-Type': 'application/json', 'Connection': 'keep-alive'}
        conn.request("POST", "/stress", body=payload, headers=headers)
        conn.getresponse().read()
    except Exception:
        try: conn.close()
        except: pass
        if f"{target_ip}:3000" in thread_local.conns:
            del thread_local.conns[f"{target_ip}:3000"]

def send_quorum_tx(idx, target_ip):
    """Inietta una transazione Ethereum pura via RPC direttamente nel nodo GoQuorum (Porta 8545)"""
    conn = get_connection(target_ip, 8545)
    acc = get_quorum_account(target_ip)
    
    if not acc: 
        return
        
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "eth_sendTransaction",
        "params": [{"from": acc, "to": acc, "value": "0x0"}],
        "id": idx
    })
    
    try:
        headers = {'Content-Type': 'application/json', 'Connection': 'keep-alive'}
        conn.request("POST", "/", body=payload, headers=headers)
        conn.getresponse().read()
    except Exception:
        try: conn.close()
        except: pass
        if f"{target_ip}:8545" in thread_local.conns:
            del thread_local.conns[f"{target_ip}:8545"]

def main():
    if len(sys.argv) < 4:
        print("Usage: python3 direct_burst.py <lab_type> <N> [node_ips...]")
        sys.exit(1)
        
    lab_type = sys.argv[1].lower()
    n = int(sys.argv[2])
    ips = sys.argv[3:]
    
    print(f"--- Raw Blockchain Injection START (Type={lab_type}, N={n}) ---")
    
    start_time = time.time()
    max_workers = min(500, n)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for i in range(n):
            target = ips[i % len(ips)]
            if lab_type == "cometbft":
                executor.submit(send_comet_tx, i, target)
            else:
                executor.submit(send_quorum_tx, i, target)
                
    duration = time.time() - start_time
    print(f"--- Raw Blockchain Injection END (Burst Time: {duration:.2f}s) ---")

if __name__ == "__main__":
    main()