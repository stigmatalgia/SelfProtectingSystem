# Script di benchmark per la capacità della blockchain (senza batching attivo).
# Esegue raffiche di alert diretti ai nodi e misura il tasso di successo delle transazioni.

import argparse
import subprocess
import time
import os
import json
import matplotlib.pyplot as plt
from concurrent.futures import ThreadPoolExecutor
import sys
import io
import os

# Configurazione dell'ambiente per l'uso di UTF-8
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['LC_ALL'] = 'C.UTF-8'
os.environ['LANG'] = 'C.UTF-8'

# Protezione contro errori di codifica in ambienti Kathara
if sys.stdout.encoding is None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding is None:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def run_cmd(cmd):
    """Esegue un comando shell e restituisce l'output standard."""
    res = subprocess.run(cmd, shell=True, capture_output=True)
    if res.returncode != 0:
        return ""
    return res.stdout.decode('utf-8', errors='replace').strip()

def get_blockchain_tx_count(lab_dir):
    """Restituisce il numero totale di transazioni interrogate tramite JSON-RPC sui nodi."""
    lab_type = "cometbft" if "cometbft" in lab_dir.lower() else "quorum"
    nodes = ["light0", "light1", "light2"] if lab_type == "cometbft" else ["member0", "member1", "member2"]
    
    total_tx = 0
    for node in nodes:
            # 1. Get account from blockchain_api (/alive) con timeout
            try:
                alive_out = run_cmd(f"kathara exec -d {lab_dir} {node} -- curl -s --max-time 15 http://localhost:3000/alive")
                if not alive_out: 
                    print(f"Warning: Node {node} API unreachable (timeout or crash).")
                    continue
                data = json.loads(alive_out)
                account = data.get('account')
                if not account: continue
                
                # 2. Get tx count via JSON-RPC (eth_getTransactionCount) con timeout
                payload = {"jsonrpc":"2.0","method":"eth_getTransactionCount","params":[account,"latest"],"id":1}
                rpc_out = run_cmd(f"kathara exec -d {lab_dir} {node} -- curl -s --max-time 15 -X POST -H 'Content-Type: application/json' -d '{json.dumps(payload)}' http://localhost:8545")
                if not rpc_out:
                    print(f"Warning: Node {node} RPC unreachable.")
                    continue
                rpc_data = json.loads(rpc_out)
                tx_count = int(rpc_data['result'], 16)
                total_tx += tx_count
            except Exception:
                pass
    return total_tx

def get_node_ips(lab_dir):
    """Restituisce la lista degli IP dei nodi (light o member) in base al tipo di lab."""
    # We use the blockchain network IPs (10.99.0.x) for direct communication
    if "cometbft" in lab_dir.lower():
        return ["10.99.0.11", "10.99.0.12", "10.99.0.13"]
    else:
        # member0, 1, 2 only. member3 is reserved for actuator
        return ["10.99.0.11", "10.99.0.12", "10.99.0.13"]


def main():
    parser = argparse.ArgumentParser(description="Benchmark di capacità blockchain.")
    parser.add_argument("lab_dir", help="Path to the Kathara lab directory")
    parser.add_argument("--steps", nargs="+", type=int, default=[10, 50, 100, 200, 500, 1000, 2000, 3000, 5000], 
                        help="List of burst sizes N to test.")
    parser.add_argument("--threads", type=int, default=100, help="Max threads for internal burst.")
    parser.add_argument("--settle-time", type=int, default=5, help="Wait time after burst.")
    args = parser.parse_args()

    lab_type = "cometbft" if "cometbft" in args.lab_dir.lower() else "quorum"
    trigger_node = "fullnode0" if lab_type == "cometbft" else "member3"
    res_dir = f"result/{lab_type}"
    os.makedirs(res_dir, exist_ok=True)
    
    # Propagate the burst script
    print("Syncing blockchain_burst.py to /shared...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    burst_script = os.path.abspath(os.path.join(script_dir, "blockchain_burst.py"))
    
    if not os.path.exists(burst_script):
        print(f"Error: Could not find script: {burst_script}")
        sys.exit(1)

    try:
        with open(burst_script, "r") as f:
            content = f.read()
        lab_shared = os.path.abspath(os.path.join(args.lab_dir, "shared", "blockchain_burst.py"))
        with open(lab_shared, "w") as f:
            f.write(content)
        print(f"Propagated {burst_script} to {lab_shared}")
    except Exception as e:
        print(f"Error: Could not sync script: {e}")
        sys.exit(1)

    node_ips = get_node_ips(args.lab_dir)
    print(f"=== Starting Blockchain-Only Capacity Benchmark on {lab_type} ===")
    print(f"Steps: {args.steps}")
    print(f"Target IPs: {node_ips}")

    results = []
    max_safe_n = 0

    for n in args.steps:
        print(f"\n--- Testing Burst Size: N={n} ---")
        baseline_tx = get_blockchain_tx_count(args.lab_dir)
        print(f"Baseline Tx: {baseline_tx}")

        # Lancio della raffica (burst) da un nodo della rete
        ips_str = " ".join(node_ips)
       # ... [codice precedente di avvio burst] ...
        print(f"Firing {n} stress requests from {trigger_node}...")
        
        
        burst_out = run_cmd(f"kathara exec -d {args.lab_dir} {trigger_node} -- python3 /shared/blockchain_burst.py {n} {ips_str}")
        print(burst_out)
        start_processing_time = time.time()

        
        # Polling: attesa che il numero di transazioni si stabilizzi
        print("Polling for transactions to settle...")
        last_tx = -1
        stable_count = 0
        while True:
            current_tx = get_blockchain_tx_count(args.lab_dir)
            diff = current_tx - baseline_tx
            if diff >= n:
                break # Target reached
            
            if current_tx == last_tx:
                stable_count += 1
                if stable_count >= 5: # No change for 10s
                    break
            else:
                stable_count = 0
            
            print(f"Progress: Tx={diff}/{n}")
            last_tx = current_tx
            time.sleep(2) # Polling slower for stability

        # IL SISTEMA SI E' STABILIZZATO. FERMA IL CRONOMETRO.
        end_processing_time = time.time()
        
        final_tx = get_blockchain_tx_count(args.lab_dir)
        diff_tx = final_tx - baseline_tx
        success_rate = (diff_tx / n) * 100 if n > 0 else 100
        
        # CALCOLO TPS (Transazioni catturate diviso i secondi impiegati a digerirle)
        total_time = end_processing_time - start_processing_time
        tps = diff_tx / total_time if total_time > 0 else 0
        
        print(f"Results for N={n}: Sent={n}, Captured={diff_tx}, Success={success_rate:.1f}%, Time={total_time:.2f}s, TPS={tps:.2f}")
        
        results.append({
            "N": n,
            "Sent": n,
            "Transactions": diff_tx,
            "SuccessRate": success_rate,
            "TotalTimeSeconds": total_time,
            "TPS": tps
        })
        if success_rate >= 99.0:
            max_safe_n = n

    # Save results
    data_file = os.path.join(res_dir, "blockchain_capacity.json")
    with open(data_file, "w") as f:
        json.dump(results, f, indent=4)
    print(f"\nRaw data saved to {data_file}")


    # Generate Chart
    n_vals = [r["N"] for r in results]
    sent_vals = [r["Sent"] for r in results]
    tx_vals = [r["Transactions"] for r in results]

    plt.figure(figsize=(10, 6))
    plt.plot(n_vals, sent_vals, 'o--', label='Alerts Sent (direct)', color='blue')
    plt.plot(n_vals, tx_vals, 's-', label='Transactions Captured', color='green')
    
    plt.xlabel('Burst Size (N)')
    plt.ylabel('Count')
    plt.title(f'Blockchain Direct Throughput Analysis - {lab_type.capitalize()}')
    plt.legend()
    plt.grid(True, which="both", ls="-", alpha=0.5)
    
    chart_file = os.path.join(res_dir, "blockchain_capacity_chart.png")
    plt.savefig(chart_file)
    plt.close()
    print(f"Chart generated: {chart_file}")

if __name__ == "__main__":
    main()
