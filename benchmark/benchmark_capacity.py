import argparse
import subprocess
import time
import os
import json
import sys
import io
from pathlib import Path

os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['LC_ALL'] = 'C.UTF-8'
os.environ['LANG'] = 'C.UTF-8'

if sys.stdout.encoding is None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding is None:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


SCRIPT_DIR = Path(__file__).resolve().parent


def run_cmd(cmd):
    res = subprocess.run(cmd, shell=True, capture_output=True)
    if res.returncode != 0:
        if res.stderr:
            stderr = res.stderr.decode('utf-8', errors='replace').strip()
            print(f"Command failed: {cmd}\nError: {stderr}", file=sys.stderr)
        return ""
    return res.stdout.decode('utf-8', errors='replace').strip()


def set_benchmark_mode(lab_dir, enabled):
    """Toggle marker used by alert_forwarder to suppress negative/recovery alerts."""
    marker = os.path.abspath(os.path.join(lab_dir, "shared", "disable_negative_alerts"))
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    if enabled:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("1\n")
    else:
        if os.path.exists(marker):
            os.remove(marker)

def set_ledger_dedup_override(lab_dir, disable_dedup):
    """Toggle marker that disables ledger state-based deduplication (CometBFT only)."""
    marker = os.path.abspath(os.path.join(lab_dir, "shared", "disable_ledger_dedup"))
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    if disable_dedup:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("1\n")
    else:
        if os.path.exists(marker):
            os.remove(marker)

def get_ids_counts(lab_dir):
    try:
        snort = int(run_cmd(f"kathara exec -d {lab_dir} ids_snort -- sh -c 'cat /var/log/snort/alert_fast.txt 2>/dev/null | wc -l'") or 0)
        suricata = int(run_cmd(f"kathara exec -d {lab_dir} ids_suricata -- sh -c 'cat /var/log/suricata/fast.log 2>/dev/null | wc -l'") or 0)
        zeek = int(run_cmd(f"kathara exec -d {lab_dir} ids_zeek -- sh -c 'grep -v \"^#\" /var/log/zeek/signatures.log 2>/dev/null | wc -l'") or 0)
        return max(snort, suricata, zeek)
    except Exception as e:
        print(f"Warning: error reading IDS counts: {e}")
        return 0

def get_blockchain_tx_count(lab_dir):
    lab_type = "cometbft" if "cometbft" in lab_dir.lower() else "quorum"
    if lab_type == "cometbft":
        try:
            rpc_out = run_cmd(f"kathara exec -d {lab_dir} light0 -- curl -s http://localhost:3000/tx_count")
            if not rpc_out:
                return 0
            rpc_data = json.loads(rpc_out)
            return rpc_data.get('count', 0)
        except Exception:
            return 0

    nodes = ["light0", "light1", "light2"] if lab_type == "cometbft" else ["member0", "member1", "member2"]
    
    total_tx = 0
    for node in nodes:
        try:
            if lab_type == "cometbft":
                rpc_out = run_cmd(f"kathara exec -d {lab_dir} {node} -- curl -s http://localhost:3000/tx_count")
                if not rpc_out: continue
                rpc_data = json.loads(rpc_out)
                tx_count = rpc_data.get('count', 0)
                total_tx += tx_count
            else:
                alive_out = run_cmd(f"kathara exec -d {lab_dir} {node} -- curl -s http://localhost:3000/alive")
                if not alive_out: continue
                data = json.loads(alive_out)
                account = data.get('account')
                if not account: continue
                
                payload = {"jsonrpc":"2.0","method":"eth_getTransactionCount","params":[account,"latest"],"id":1}
                rpc_out = run_cmd(f"kathara exec -d {lab_dir} {node} -- curl -s -X POST -H 'Content-Type: application/json' -d '{json.dumps(payload)}' http://localhost:8545")
                if not rpc_out: continue
                rpc_data = json.loads(rpc_out)
                tx_count = int(rpc_data['result'], 16)
                total_tx += tx_count
        except Exception:
            pass
    return total_tx

def get_blockchain_alert_count(lab_dir):
    lab_type = "cometbft" if "cometbft" in lab_dir.lower() else "quorum"
    nodes = ["light0", "light1", "light2"] if lab_type == "cometbft" else ["member0", "member1", "member2"]
    
    total_processed = 0
    total_received = 0
    for node in nodes:
        try:
            stats_out = run_cmd(f"kathara exec -d {lab_dir} {node} -- curl -s http://localhost:3000/stats")
            if not stats_out: continue
            data = json.loads(stats_out)
            total_processed += data.get('totalAlertsProcessed', 0)
            total_received += data.get('totalAlertsReceived', 0)
        except Exception:
            pass
    return total_processed, total_received

def reset_system_state(lab_dir):
    print("Resetting system state to SAFE (0000)...")
    lab_type = "cometbft" if "cometbft" in lab_dir.lower() else "quorum"
    nodes = ["light0", "light1", "light2"] if lab_type == "cometbft" else ["member0", "member1", "member2"]

    if lab_type == "cometbft":
        # In benchmark mode /alert ignores value=0 updates; use /stress to force
        # per-node state back to 0 so next step dedup starts from a clean baseline.
        reset_types = ["SQL_INJECTION", "XSS_ATTACK", "PATH_TRAVERSAL", "COMMAND_INJECTION"]
        for node in nodes:
            for alert_type in reset_types:
                payload = json.dumps({"type": alert_type, "value": 0})
                try:
                    run_cmd(
                        f"kathara exec -d {lab_dir} {node} -- "
                        f"curl -s -X POST -H 'Content-Type: application/json' "
                        f"-d '{payload}' http://localhost:3000/stress"
                    )
                except Exception as e:
                    print(f"Warning: Failed to reset node {node}/{alert_type}: {e}")
    else:
        payload = json.dumps({"type": "SAFE_ENVIRONMENT", "value": 1})
        for node in nodes:
            try:
                run_cmd(f"kathara exec -d {lab_dir} {node} -- curl -s -X POST -H 'Content-Type: application/json' -d '{payload}' http://localhost:3000/alert")
            except Exception as e:
                print(f"Warning: Failed to reset node {node}: {e}")
    
    time.sleep(3)

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
    
    print("Syncing attacker_burst.py to /shared...")
    host_script = SCRIPT_DIR / "attacker_burst.py"
    if not host_script.exists():
        alt_script = Path.cwd() / "attacker_burst.py"
        if alt_script.exists():
            host_script = alt_script
        else:
            print(f"Error: cannot find attacker burst script. Tried: {SCRIPT_DIR / 'attacker_burst.py'} and {alt_script}")
            return
    
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

    nodes = ["light0", "light1", "light2"] if lab_type == "cometbft" else ["member0", "member1", "member2"]
    print("Enabling Stress Mode on light nodes...")
    for node in nodes:
        run_cmd(f"kathara exec -d {args.lab_dir} {node} -- curl -s -X POST http://localhost:3000/stress")
    
    print(f"Checking if attacker can reach juice_shop (10.0.0.80:3000)...")
    conn_check = run_cmd(f"kathara exec -d {args.lab_dir} attacker -- curl -s -o /dev/null -w '%{{http_code}}' http://10.0.0.80:3000")
    print(f"Connectivity check: HTTP {conn_check}")
    if conn_check != "200":
        if conn_check == "000":
            print("CRITICAL Warning: Attacker cannot connect to juice_shop (Connection Refused). IDS will NOT detect any attacks!")
        else:
            print(f"Warning: Attacker reached juice_shop but returned HTTP {conn_check}. IDS might not detect attacks correctly.")
        print("Proceeding anyway, but results might be zeroed...")

    results = []
    benchmark_mode_enabled = (lab_type == "cometbft")
    ids_count = len(nodes)
    attack_types_per_step = 4
    if benchmark_mode_enabled:
        set_benchmark_mode(args.lab_dir, True)
        # For capacity benchmark, keep per-IDS step-level votes and avoid global state collapse.
        set_ledger_dedup_override(args.lab_dir, True)

    try:
        for n in args.steps:
            print(f"\n--- Testing Burst Size: N={n} ---")
        
            baseline_ids = get_ids_counts(args.lab_dir)
            baseline_tx = get_blockchain_tx_count(args.lab_dir)
            baseline_proc, baseline_recv = get_blockchain_alert_count(args.lab_dir)
            print(f"Baseline: IDs={baseline_ids}, API_Recv={baseline_recv}, API_Proc={baseline_proc}, Tx={baseline_tx}")

            print(f"Firing {n} attacks from attacker...")
            if benchmark_mode_enabled:
                burst_out = run_cmd(
                    f"kathara exec -d {args.lab_dir} attacker -- "
                    f"python3 -u /shared/attacker_burst.py {n} --pattern cycle"
                )
            else:
                burst_out = run_cmd(f"kathara exec -d {args.lab_dir} attacker -- python3 -u /shared/attacker_burst.py {n}")
            print(f"--- Attacker Output (N={n}) ---")
            print(burst_out if burst_out else "(No output from attacker)")
            print("------------------------------")

            print(f"Burst sent. Waiting {args.settle_time}s for processing...")
            time.sleep(args.settle_time)
        
            print("Polling for system to settle...")
            last_count = baseline_recv
            stable_count = 0
            expected_ingress = n * ids_count if benchmark_mode_enabled else n
            while True:
                _, current_recv = get_blockchain_alert_count(args.lab_dir)
                if current_recv >= (baseline_recv + expected_ingress):
                    break
                
                if current_recv == last_count:
                    stable_count += 1
                    if stable_count >= 3: 
                        break
                else:
                    stable_count = 0
                
                print(f"Progress: API Ingress Alerts={current_recv - baseline_recv}/{expected_ingress}")
                last_count = current_recv
                time.sleep(5)

            # Drain late-arriving ingress updates to reduce undercount at high load.
            if benchmark_mode_enabled:
                no_growth_cycles = 0
                while no_growth_cycles < 3:
                    time.sleep(2)
                    _, drained_recv = get_blockchain_alert_count(args.lab_dir)
                    if drained_recv > last_count:
                        last_count = drained_recv
                        no_growth_cycles = 0
                    else:
                        no_growth_cycles += 1

            final_ids = get_ids_counts(args.lab_dir)
            final_tx = get_blockchain_tx_count(args.lab_dir)
            final_proc, final_recv = get_blockchain_alert_count(args.lab_dir)
            
            diff_ids = final_ids - baseline_ids
            diff_tx = final_tx - baseline_tx
            diff_proc = final_proc - baseline_proc
            diff_recv = final_recv - baseline_recv

            if benchmark_mode_enabled:
                expected_sensitive = ids_count * min(attack_types_per_step, n)
            
            print(f"Results for N={n}: Sent={n}, IDs={diff_ids}, API_Recv={diff_recv}, API_Sens={diff_proc}, Tx={diff_tx}")
            results.append({
                "N": n,
                "Sent": n,
                "Detected": diff_ids,
                "Ingress": diff_recv,
                "Sensitive": diff_proc,
                "Transactions": diff_tx
            })

            reset_system_state(args.lab_dir)
    finally:
        if benchmark_mode_enabled:
            set_benchmark_mode(args.lab_dir, False)
            set_ledger_dedup_override(args.lab_dir, False)

    data_file = os.path.join(res_dir, "capacity_results.json")
    with open(data_file, "w") as f:
        json.dump(results, f, indent=4)
    print(f"\nRaw data saved to {data_file}")

if __name__ == "__main__":
    main()