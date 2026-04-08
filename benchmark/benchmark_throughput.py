import argparse
import subprocess
import time
import os
import json
import matplotlib.pyplot as plt

def run_cmd(cmd):
    """Executes a shell command and returns the stdout result. Prints stderr on failure."""
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0:
        if res.stderr:
            print(f"Command failed: {cmd}\nError: {res.stderr.strip()}", file=sys.stderr)
        return ""
    return res.stdout.strip()

def get_ids_counts(lab_dir):
    """Parses IDS logs and returns the maximum alert count among Snort, Suricata, and Zeek."""
    try:
        snort = int(run_cmd(f"kathara exec -d {lab_dir} ids_snort -- sh -c 'cat /var/log/snort/alert_fast.txt 2>/dev/null | wc -l'") or 0)
        suricata = int(run_cmd(f"kathara exec -d {lab_dir} ids_suricata -- sh -c 'cat /var/log/suricata/fast.log 2>/dev/null | wc -l'") or 0)
        zeek = int(run_cmd(f"kathara exec -d {lab_dir} ids_zeek -- sh -c 'grep -v \"^#\" /var/log/zeek/signatures.log 2>/dev/null | wc -l'") or 0)
        return max(snort, suricata, zeek)
    except Exception as e:
        print(f"Warning: error reading IDS counts: {e}")
        return 0

def get_blockchain_tx_count(lab_dir):
    """Returns the sum of transaction counts for all IDS agents by querying their JSON-RPC nodes."""
    lab_type = "cometbft" if "cometbft" in lab_dir.lower() else "quorum"
    nodes = ["light0", "light1", "light2"] if lab_type == "cometbft" else ["member0", "member1", "member2"]
    
    total_tx = 0
    for node in nodes:
        try:
            # 1. Get account from blockchain_api (/alive)
            alive_out = run_cmd(f"kathara exec -d {lab_dir} {node} -- curl -s --max-time 5 http://localhost:3000/alive")
            if not alive_out: continue
            data = json.loads(alive_out)
            account = data.get('account')
            if not account: continue
            
            # 2. Get tx count via JSON-RPC (eth_getTransactionCount)
            payload = {"jsonrpc":"2.0","method":"eth_getTransactionCount","params":[account,"latest"],"id":1}
            rpc_out = run_cmd(f"kathara exec -d {lab_dir} {node} -- curl -s --max-time 10 -X POST -H 'Content-Type: application/json' -d '{json.dumps(payload)}' http://localhost:8545")
            if not rpc_out: continue
            rpc_data = json.loads(rpc_out)
            tx_count = int(rpc_data['result'], 16)
            total_tx += tx_count
        except Exception as e:
            print(f"DEBUG: Error getting tx from {node}: {e}")
            pass
    return total_tx

def get_blockchain_alert_count(lab_dir):
    """Returns (totalAlertsProcessed, totalAlertsReceived) from all IDS agents via the /stats endpoint."""
    lab_type = "cometbft" if "cometbft" in lab_dir.lower() else "quorum"
    nodes = ["light0", "light1", "light2"] if lab_type == "cometbft" else ["member0", "member1", "member2"]
    
    total_processed = 0
    total_received = 0
    for node in nodes:
        try:
            stats_out = run_cmd(f"kathara exec -d {lab_dir} {node} -- curl -s --max-time 5 http://localhost:3000/stats")
            if not stats_out: continue
            data = json.loads(stats_out)
            total_processed += data.get('totalAlertsProcessed', 0)
            total_received += data.get('totalAlertsReceived', 0)
        except Exception as e:
            print(f"DEBUG: Error getting stats from {node}: {e}")
            pass
    return total_processed, total_received

def main():
    parser = argparse.ArgumentParser(description="Benchmark the SPS capacity by sending attack bursts of N magnitude.")
    parser.add_argument("lab_dir", help="Path to the Kathara lab directory (e.g., ../lab/quorum)")
    parser.add_argument("--steps", nargs="+", type=int, default=[10, 100, 1000, 5000, 10000, 20000, 50000], 
                        help="List of N attacks to test.")
    parser.add_argument("--settle-time", type=int, default=10, help="Wait time (seconds) after a burst before measuring.")
    args = parser.parse_args()

    lab_type = "cometbft" if "cometbft" in args.lab_dir.lower() else "quorum"
    res_dir = f"result/{lab_type}"
    os.makedirs(res_dir, exist_ok=True)

    print(f"=== Starting Capacity Benchmark on {lab_type} ===")
    print(f"Steps: {args.steps}")
    
    # 1. Pushing the burst script to attacker
    if not os.path.exists("attacker_burst.py"):
        print("Error: 'attacker_burst.py' not found in the current directory. Please create it.")
        return

    # 1. Sync and verify the burst script in /shared
    print("Syncing attacker_burst.py to /shared...")
    host_script = "attacker_burst.py"
    if not os.path.exists(host_script):
        if os.path.exists("benchmark/attacker_burst.py"):
            host_script = "benchmark/attacker_burst.py"
    
    try:
        with open(host_script, "r") as f:
            script_content = f.read()
        
        lab_shared = os.path.join(args.lab_dir, "shared", "attacker_burst.py")
        if os.path.exists(os.path.dirname(lab_shared)):
            with open(lab_shared, "w") as f:
                f.write(script_content)
            print(f"Propagated {host_script} to {lab_shared}")
    except Exception as e:
        print(f"Warning: Could not sync script: {e}")

    # 2. Enable Stress Mode in APIs
    print("Enabling Stress Mode on light nodes...")
    nodes = ["light0", "light1", "light2"] if lab_type == "cometbft" else ["member0", "member1", "member2"]
    for node in nodes:
        run_cmd(f"kathara exec -d {args.lab_dir} {node} -- curl -s -X POST http://localhost:3000/stress")
    
    # 3. Check connectivity
    print(f"Checking if attacker can reach juice_shop (10.0.0.80:3000)...")
    conn_check = run_cmd(f"kathara exec -d {args.lab_dir} attacker -- curl -s -o /dev/null -w '%{{http_code}}' http://10.0.0.80:3000")
    print(f"Connectivity check: HTTP {conn_check}")
    if conn_check != "200":
        print("Warning: Attacker cannot reach juice_shop or returned non-200. Proceeding anyway...")

    results = []

    for n in args.steps:
        print(f"\n--- Testing Burst Size: N={n} ---")
        
        # 2. Get baseline counts before the burst
        baseline_ids = get_ids_counts(args.lab_dir)
        baseline_tx = get_blockchain_tx_count(args.lab_dir)
        baseline_proc, baseline_recv = get_blockchain_alert_count(args.lab_dir)
        print(f"Baseline: IDs={baseline_ids}, API_Recv={baseline_recv}, API_Proc={baseline_proc}, Tx={baseline_tx}")

        # 3. Trigger the burst
        print(f"Firing {n} attacks from attacker...")
        # Use python3 -u for unbuffered output
        burst_out = run_cmd(f"kathara exec -d {args.lab_dir} attacker -- python3 -u /shared/attacker_burst.py {n}")
        print(f"--- Attacker Output (N={n}) ---")
        print(burst_out if burst_out else "(No output from attacker)")
        print("------------------------------")

        # 4. Wait for the system to settle
        print(f"Burst sent. Waiting {args.settle_time}s for processing...")
        time.sleep(args.settle_time)
        
        # Polling: wait until count stops increasing for at least 3 consecutive checks
        print("Polling for system to settle...")
        last_count = baseline_recv
        stable_count = 0
        while True:
            _, current_recv = get_blockchain_alert_count(args.lab_dir)
            if current_recv >= (baseline_recv + n):
                break # Target reached
            
            if current_recv == last_count:
                stable_count += 1
                if stable_count >= 3: 
                    break
            else:
                stable_count = 0
            
            print(f"Progress: API Ingress Alerts={current_recv - baseline_recv}/{n} (Current: {current_recv}, Baseline: {baseline_recv})")
            last_count = current_recv
            time.sleep(5)

        # 5. Measure final counts
        final_ids = get_ids_counts(args.lab_dir)
        final_tx = get_blockchain_tx_count(args.lab_dir)
        final_proc, final_recv = get_blockchain_alert_count(args.lab_dir)
        
        diff_ids = final_ids - baseline_ids
        diff_tx = final_tx - baseline_tx
        diff_proc = final_proc - baseline_proc
        diff_recv = final_recv - baseline_recv
        
        print(f"Results for N={n}: Sent={n}, IDs={diff_ids}, API_Recv={diff_recv}, API_Sens={diff_proc}, Tx={diff_tx}")
        results.append({
            "N": n,
            "Sent": n,
            "Detected": diff_ids,
            "Ingress": diff_recv,
            "Sensitive": diff_proc,
            "Transactions": diff_tx
        })

    # 6. Save results
    data_file = os.path.join(res_dir, "capacity_results.json")
    with open(data_file, "w") as f:
        json.dump(results, f, indent=4)
    print(f"\nRaw data saved to {data_file}")

    # 7. Generate Chart
    sent_vals = [r["Sent"] for r in results]
    detected_vals = [r["Detected"] for r in results]
    ingress_vals = [r["Ingress"] for r in results]
    sensitive_vals = [r["Sensitive"] for r in results]
    tx_vals = [r["Transactions"] for r in results]

    plt.figure(figsize=(12, 6))
    
    # Use log scale if N range is very large
    plt.xscale('log' if args.steps[-1] >= 1000 else 'linear')
    
    plt.plot(args.steps, sent_vals, 'o--', label='Attacks Sent (Metric A)', color='blue')
    plt.plot(args.steps, detected_vals, 's-', label='Detected by IDS (Metric B)', color='orange')
    plt.plot(args.steps, ingress_vals, 'p-', label='API Ingress (Raw Alerts)', color='purple')
    plt.plot(args.steps, sensitive_vals, '^-', label='Sensitive Updates (Metric C)', color='green')
    plt.plot(args.steps, tx_vals, 'x:', label='Blockchain Tx (Efficiency)', color='gray')

    plt.xlabel('Order of Magnitude (Burst Size N)')
    plt.ylabel('Count of Events')
    plt.title(f'SPS Capacity Analysis - {lab_type.capitalize()} Environment')
    plt.legend()
    plt.grid(True, which="both", ls="-", alpha=0.5)
    
    chart_file = os.path.join(res_dir, "capacity_chart.png")
    plt.savefig(chart_file)
    plt.close()
    print(f"Chart generated: {chart_file}")

if __name__ == "__main__":
    main()