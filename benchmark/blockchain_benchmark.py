import argparse
import subprocess
import time
import os
import sys
import json
import io
import re


os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LC_ALL",           "C.UTF-8")
os.environ.setdefault("LANG",             "C.UTF-8")

if sys.stdout.encoding is None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr.encoding is None:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


# Mirrors generate_cometbft_config.py
COMET_AGENTS   = ["light0", "light1", "light2"]
COMET_BENCH_NODE   = "light0"          # exec target: sps-bench runs here
COMET_P2P_TARGET   = "127.0.0.1:26656" # loopback P2P port (node's own listener)
COMET_API_TARGET   = "127.0.0.1:3000"  # loopback sps-node API
COMET_RPC_TARGETS  = ["10.99.0.1:26657", "10.99.0.2:26657", "10.99.0.3:26657"]
COMET_P2P_PORT     = 26656
COMET_API_PORT     = 26657

QUORUM_NODES       = ["member0", "member1", "member2"]
QUORUM_BENCH_NODE  = "member3"




def run_cmd(cmd: str, timeout: int = 300) -> str:
    """Run a shell command, return stdout (empty string on failure)."""
    try:
        res = subprocess.run(
            cmd, shell=True, capture_output=True, timeout=timeout
        )
        if res.returncode != 0 and res.stderr:
            print(f"[cmd err] {res.stderr.decode('utf-8', 'replace').strip()[:300]}",
                  file=sys.stderr)
        return res.stdout.decode("utf-8", errors="replace").strip()
    except subprocess.TimeoutExpired:
        print(f"[cmd timeout] {cmd[:120]}", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"[cmd exception] {e}", file=sys.stderr)
        return ""


def kathara_exec(lab_dir: str, node: str, inner_cmd: str, timeout: int = 600) -> str:
    """Execute a command inside a Kathara container."""
    cmd = f"kathara exec -d {lab_dir} {node} -- {inner_cmd}"
    return run_cmd(cmd, timeout=timeout)




SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
COMET_BENCH_SRC_DIR = os.path.join(REPO_ROOT, "lab", "cometbft", "shared", "sps-bench")
QUORUM_BENCH_JS_SRC = os.path.join(REPO_ROOT, "lab", "quorum", "shared", "quorum_native_bench.js")


def _shared_path(lab_dir: str, filename: str) -> str:
    return os.path.abspath(os.path.join(lab_dir, "shared", filename))


def sync_file(src: str, lab_dir: str, dest_name: str | None = None):
    """Copy a host file into <lab_dir>/shared/."""
    dest = _shared_path(lab_dir, dest_name or os.path.basename(src))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    
    # Avoid emptying the file if src and dest are the same
    if os.path.exists(dest) and os.path.samefile(src, dest):
        print(f"  [sync] {src} is already at {dest}")
        return

    with open(src, "rb") as fsrc, open(dest, "wb") as fdst:
        fdst.write(fsrc.read())
    print(f"  Synced {src}  →  {dest}")


def set_benchmark_mode(lab_dir: str, enabled: bool):
    """Toggle benchmark mode marker used by alert_forwarder to disable negative alerts."""
    marker = os.path.abspath(os.path.join(lab_dir, "shared", "disable_negative_alerts"))
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    if enabled:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("1\n")
    else:
        if os.path.exists(marker):
            os.remove(marker)


def set_raw_throughput_mode(lab_dir: str, enabled: bool):
    """Toggle marker used by sps-chain ledger to disable benchmark-time dedup."""
    marker = os.path.abspath(os.path.join(lab_dir, "shared", "disable_ledger_dedup"))
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    if enabled:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("1\n")
    else:
        if os.path.exists(marker):
            os.remove(marker)


def _try_build_host(bench_src_dir: str) -> str | None:
    """
    Attempt to build sps-bench on the host with musl target.
    Returns path to binary, or None on failure.
    """
    binary = os.path.join(bench_src_dir,
                          "target", "x86_64-unknown-linux-musl",
                          "release", "sps-bench")
    cmd = (
        f"cargo build --release --target x86_64-unknown-linux-musl "
        f"--manifest-path {os.path.join(bench_src_dir, 'Cargo.toml')} "
        f"2>&1"
    )
    print("  Building sps-bench (host, musl)…")
    result = subprocess.run(cmd, shell=True, capture_output=False, timeout=300)
    if result.returncode == 0 and os.path.isfile(binary):
        print(f"  Build OK → {binary}")
        return binary
    print("  Host musl build failed — will compile inside container.")
    return None


def _build_inside_container(lab_dir: str, node: str) -> bool:
    """
    Compile sps-bench inside the running Kathara container.
    The sps-chain Cargo workspace is available at /shared/sps-chain/.
    We add a temporary [[bin]] target pointing at /shared/sps-bench.rs.
    """
    print(f"  Compiling sps-bench inside container {node}…")

    # The source file is already synced to /shared/sps-bench.rs.
    # We need a Cargo.toml to build a standalone binary from it.
    toml_src = os.path.join(COMET_BENCH_SRC_DIR, "Cargo.toml")
    sync_file(toml_src,
              lab_dir,
              "sps-bench-Cargo.toml")

    # Build using the minimal standalone Cargo.toml inside the container.
    inner = (
        "cargo build --release "
        "--manifest-path /shared/sps-bench-Cargo.toml "
        "--target-dir /tmp/sps-bench-build "
        "&& cp /tmp/sps-bench-build/release/sps-bench /shared/sps-bench"
    )
    out = kathara_exec(lab_dir, node, f"bash -c '{inner}'", timeout=600)
    # Check the binary exists in the container
    check = kathara_exec(lab_dir, node, "test -x /shared/sps-bench && echo OK")
    if "OK" in check:
        print("  In-container build succeeded.")
        return True
    print(f"  In-container build FAILED.\n  Output: {out[:500]}")
    return False


def prepare_sps_bench(lab_dir: str) -> bool:
    """
    Ensure `sps-bench` is available.
    
    Priority:
      1. /usr/local/bin/sps-bench inside container (baked into image)
            2. lab/cometbft/shared/sps-bench/sps-bench on host (synced to /shared/sps-bench)
      3. /shared/sps-bench (synced previously)
    """
    print("\n[prepare] Checking sps-bench availability…")
    
    # Check if already in container path (baked in)
    check = kathara_exec(lab_dir, COMET_BENCH_NODE, "which sps-bench")
    if "/usr/local/bin/sps-bench" in check or "sps-bench" in check:
        print("  Found baked-in binary: /usr/local/bin/sps-bench")
        return True

    # Fallback: check if we have a pre-built binary on host to sync
    bench_dir = COMET_BENCH_SRC_DIR
    prebuilt = os.path.join(bench_dir, "sps-bench")
    target_bin = os.path.join(
        bench_dir,
        "target", "x86_64-unknown-linux-musl", "release", "sps-bench"
    )
    if not os.path.isfile(prebuilt) and os.path.isfile(target_bin):
        prebuilt = target_bin
    if os.path.isfile(prebuilt):
        print(f"  Found pre-built binary on host: {prebuilt}")
        sync_file(prebuilt, lab_dir, "sps-bench")
        return True

    # Final fallback: sync source and try to compile (likely to fail without cargo)
    rs_src = os.path.join(bench_dir, "sps-bench.rs")
    if os.path.isfile(rs_src):
        sync_file(rs_src, lab_dir, "sps-bench.rs")
        print("  Source synced. Attempting in-container build (requires cargo)…")
        return _build_inside_container(lab_dir, COMET_BENCH_NODE)

    return False


def prepare_quorum_bench(lab_dir: str) -> bool:
    """Copy quorum_native_bench.js into /shared."""
    js_src = QUORUM_BENCH_JS_SRC
    if not os.path.isfile(js_src):
        print(f"  ERROR: {js_src} not found.")
        return False
    sync_file(js_src, lab_dir, "quorum_native_bench.js")
    return True




def get_node_tx_count(lab_dir: str, node: str, lab_type: str) -> int:
    """Return the committed tx count reported by a single node's /tx_count."""
    if lab_type == "cometbft":
        metrics = get_comet_rpc_metrics(lab_dir, node)
        if metrics is None:
            return 0
        return metrics["total_txs"]
    else:
        # Quorum: read tx nonce for the same account source used by quorum_native_bench.js.
        # Prefer eth_accounts[0] from the node RPC; fall back to /alive account if needed.
        accounts_payload = json.dumps({
            "jsonrpc": "2.0", "method": "eth_accounts", "params": [], "id": 1
        })
        accounts_rpc = kathara_exec(
            lab_dir, node,
            f"curl -s --max-time 10 -X POST "
            f"-H 'Content-Type: application/json' "
            f"-d '{accounts_payload}' http://127.0.0.1:8545"
        )

        account = None
        try:
            account_list = json.loads(accounts_rpc).get("result", [])
            if isinstance(account_list, list) and account_list:
                account = account_list[0]
        except Exception:
            account = None

        if not account:
            alive = kathara_exec(
                lab_dir, node,
                "curl -s --max-time 10 http://127.0.0.1:3000/alive"
            )
            if alive:
                try:
                    account = json.loads(alive).get("account")
                except Exception:
                    account = None

        if not account:
            return 0

        def _query_nonce(tag: str) -> int:
            payload = json.dumps({
                "jsonrpc": "2.0", "method": "eth_getTransactionCount",
                "params": [account, tag], "id": 1
            })
            rpc = kathara_exec(
                lab_dir, node,
                f"curl -s --max-time 10 -X POST "
                f"-H 'Content-Type: application/json' "
                f"-d '{payload}' http://127.0.0.1:8545"
            )
            if not rpc:
                return 0
            try:
                return int(json.loads(rpc).get("result", "0x0"), 16)
            except Exception:
                return 0

        # "pending" is usually the closest metric to what the JS bench increments with nonce.
        # Keep "latest" as fallback so we don't regress if pending is unsupported.
        latest = _query_nonce("latest")
        pending = _query_nonce("pending")
        return max(latest, pending)


def wait_for_tx_quiescence(
    lab_dir: str,
    lab_type: str,
    max_wait_s: int = 10,
    poll_s: float = 1.0,
    stable_ticks: int = 15,
) -> int:
    """
    Wait until cluster tx_count stops moving for a short period.
    Returns the latest observed tx_count.
    """
    last = get_primary_tx_count(lab_dir, lab_type)
    stable = 0
    deadline = time.time() + max_wait_s

    while time.time() < deadline:
        time.sleep(poll_s)
        current = get_primary_tx_count(lab_dir, lab_type)
        if current == last:
            stable += 1
            if stable >= stable_ticks:
                return current
        else:
            stable = 0
            last = current

    return last


def get_cluster_tx_counts(lab_dir: str, nodes: list[str], lab_type: str) -> dict[str, int]:
    """Return per-node tx counts for the given node list."""
    return {n: get_node_tx_count(lab_dir, n, lab_type) for n in nodes}


def get_primary_tx_count(lab_dir: str, lab_type: str) -> int:
    """
    Return the tx count of the primary benchmark node or cluster.
    For CometBFT this is light0.
    For Quorum this is the aggregate of member0, member1, member2 (distributed load).
    """
    if lab_type == "cometbft":
        return get_node_tx_count(lab_dir, COMET_BENCH_NODE, lab_type)
    else:
        total = 0
        for node in ["member0", "member1", "member2"]:
            total += get_node_tx_count(lab_dir, node, lab_type)
        return total




def _rpc_int(value, default: int = 0) -> int:
    try:
        return int(str(value))
    except Exception:
        return default


def get_comet_rpc_metrics(lab_dir: str, node: str = COMET_BENCH_NODE) -> dict | None:
    """
    Query CometBFT RPC and sps-node API to return:
      - total_txs (from sps-node /tx_count on port 3000)
      - unconfirmed_txs (from CometBFT /num_unconfirmed_txs on port 26657)
    """
    # 1. Get total committed txs from sps-node API
    tx_count_raw = kathara_exec(
        lab_dir, node, "curl -s --max-time 2 http://127.0.0.1:3000/tx_count", timeout=5
    )
    
    # 2. Get unconfirmed txs from CometBFT RPC
    unconfirmed_raw = kathara_exec(
        lab_dir, node, "curl -s --max-time 2 http://127.0.0.1:26657/num_unconfirmed_txs", timeout=5
    )
    
    if not tx_count_raw or not unconfirmed_raw:
        return None
        
    try:
        tx_count_j = json.loads(tx_count_raw)
        unconfirmed_j = json.loads(unconfirmed_raw)
        
        total_txs = _rpc_int(tx_count_j.get("count", 0), default=0)
        unconfirmed_txs = _rpc_int(
            unconfirmed_j.get("result", {}).get("total", 0), default=0
        )
        
        return {
            "total_txs": total_txs,
            "unconfirmed_txs": unconfirmed_txs,
        }
    except Exception:
        return None


def wait_for_comet_completion(
    lab_dir: str,
    baseline_total_txs: int,
    n: int,
    timeout_s: int = 600,
    poll_s: float = 2.0,
) -> tuple[float | None, dict | None]:
    """
    Wait until:
      total_txs >= baseline_total_txs + n
      and unconfirmed_txs == 0
    Returns (completion_timestamp, final_metrics) or (None, last_metrics on timeout/failure).
    """
    target_total = baseline_total_txs + n
    deadline = time.time() + timeout_s
    last_metrics = None

    while time.time() < deadline:
        metrics = get_comet_rpc_metrics(lab_dir, COMET_BENCH_NODE)
        if metrics is not None:
            last_metrics = metrics
            if metrics["total_txs"] >= target_total and metrics["unconfirmed_txs"] == 0:
                return time.time(), metrics
        time.sleep(poll_s)

    return None, last_metrics


_BENCH_STATS_RE = re.compile(r"BENCH_STATS:(\{.*\})")


def parse_bench_stats(output: str) -> dict | None:
    """Extract and parse the BENCH_STATS JSON line from sps-bench / quorum_native_bench."""
    for line in reversed(output.splitlines()):
        m = _BENCH_STATS_RE.search(line)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    return None


def run_cometbft_bench(
    lab_dir: str,
    n: int,
    step: int,
    concurrency: int,
    targets: list[str],
) -> dict | None:
    """
    Execute sps-bench inside the light0 container.
    Returns parsed BENCH_STATS dict or None on failure.
    """
    targets_arg = ",".join(targets)
    cmd_str = (
        f"sps-bench --n {n} "
        f"--targets {targets_arg} "
        f"--concurrency {concurrency} "
        f"--step {step} "
        f"|| /shared/sps-bench --n {n} --targets {targets_arg} "
        f"--concurrency {concurrency} --step {step}"
    )
    inner = f"bash -c '{cmd_str}'"
    print(f"  Executing: {inner}")
    # Timeout: generous — the binary polls /tx_count for up to 60s.
    raw = kathara_exec(lab_dir, COMET_BENCH_NODE, inner, timeout=900)
    print(raw)
    stats = parse_bench_stats(raw)
    if stats is None:
        print(f"  WARNING: No BENCH_STATS found in output.", file=sys.stderr)
    return stats


def wait_for_quorum_ready(lab_dir: str):
    """
    Check if member3 has the contract artifacts AND Geth RPC is up.
    """
    print("  [pre] Waiting for Quorum initialization (member3)...")
    check_file = "ls /home/qbft/data/contract_address.txt"
    check_rpc = "curl -s -X POST -H 'Content-Type: application/json' --data '{\"jsonrpc\":\"2.0\",\"method\":\"eth_accounts\",\"params\":[],\"id\":1}' http://127.0.0.1:8545"
    
    for i in range(60):
        try:
            # Check file
            raw_f = kathara_exec(lab_dir, QUORUM_BENCH_NODE, check_file, timeout=5)
            file_ok = "not found" not in raw_f.lower() and "contract_address.txt" in raw_f
            
            # Check RPC
            raw_r = kathara_exec(lab_dir, QUORUM_BENCH_NODE, check_rpc, timeout=5)
            rpc_ok = "result" in raw_r.lower()
            
            if file_ok and rpc_ok:
                print("  [pre] Quorum ready (Contract & RPC)!")
                return True
        except:
            pass
        time.sleep(2)
        if i % 10 == 0 and i > 0:
            print(f"  [pre] Still waiting for Quorum... ({i*2}s)")
    return False


def wait_for_comet_ready(lab_dir: str):
    """
    Check if CometBFT RPC AND sps-node API are responding.
    """
    print("  [pre] Waiting for CometBFT and sps-node initialization (light0)...")
    check_cmd = (
        "bash -lc \""
        "curl -s --max-time 1 http://127.0.0.1:26657/status && "
        "curl -s --max-time 1 http://127.0.0.1:3000/alive\""
    )
    for i in range(60):
        try:
            raw = kathara_exec(lab_dir, COMET_BENCH_NODE, check_cmd, timeout=5)
            if raw and "alive" in raw.lower():
                metrics = get_comet_rpc_metrics(lab_dir, COMET_BENCH_NODE)
                if metrics is not None:
                    print("  [pre] CometBFT and sps-node are up.")
                    return True
        except:
            pass
        time.sleep(2)
        if i % 10 == 0 and i > 0:
            print(f"  [pre] Still waiting for CometBFT/sps-node... ({i*2}s)")
    return False


def run_quorum_bench(lab_dir: str, n: int) -> dict | None:
    """
    Execute quorum_native_bench.js inside member3.
    Returns parsed BENCH_STATS dict or None on failure.
    """
    # web3 is installed in /home/qbft/node_modules by the Dockerfile.
    # Must use bash -c to correctly interpret environment variable assignment.
    quorum_concurrency = os.environ.get("QUORUM_BENCH_CONCURRENCY", "").strip()
    env_prefix = "NODE_PATH=/home/qbft/node_modules "
    if quorum_concurrency:
        env_prefix += f"QUORUM_BENCH_CONCURRENCY={int(quorum_concurrency)} "

    inner = (
        "bash -c "
        f"'{env_prefix}node /shared/quorum_native_bench.js {n}'"
    )
    print(f"  Executing: {inner}")
    raw = kathara_exec(lab_dir, QUORUM_BENCH_NODE, inner, timeout=900)
    print(raw)
    stats = parse_bench_stats(raw)
    if stats is None:
        print(f"  WARNING: No BENCH_STATS found in output.", file=sys.stderr)
    return stats




def main():
    parser = argparse.ArgumentParser(
        description="Native SPS blockchain throughput benchmark (P2P injection)."
    )
    parser.add_argument("lab_dir",
                        help="Path to the Kathara lab directory")
    parser.add_argument("--steps", nargs="+", type=int,
                        default=[100, 500, 1000, 5000, 10000, 20000, 50000, 75000, 100000],
                        help="Burst sizes N to benchmark.")
    parser.add_argument("--concurrency", type=int,
                        default=int(os.environ.get("COMET_BENCH_CONCURRENCY", "128")),
                        help="Parallel WebSocket send workers used by sps-bench (CometBFT only).")
    parser.add_argument("--comet-rpc-targets", type=str,
                        default=os.environ.get("COMET_RPC_TARGETS", ",".join(COMET_RPC_TARGETS)),
                        help="Comma-separated CometBFT RPC targets for direct WebSocket injection.")
    parser.add_argument("--no-build", action="store_true",
                        help="Skip binary compilation; assume sps-bench exists in /shared.")
    args = parser.parse_args()

    lab_type = "cometbft" if "cometbft" in args.lab_dir.lower() else "quorum"
    res_dir  = os.path.join("result", lab_type)
    os.makedirs(res_dir, exist_ok=True)

    print(f"\n{'=' * 60}")
    print(f"  Native Blockchain Throughput Benchmark — {lab_type.upper()}")
    print(f"{'=' * 60}")
    print(f"  Lab directory : {args.lab_dir}")
    print(f"  Burst steps   : {args.steps}")
    print(f"  Concurrency   : {args.concurrency}  (CometBFT only)")
    print(f"  RPC targets   : {args.comet_rpc_targets}  (CometBFT only)")
    print(f"  Results dir   : {res_dir}/")
    print(f"{'=' * 60}\n")


    if not args.no_build:
        if lab_type == "cometbft":
            ok = prepare_sps_bench(args.lab_dir)
        else:
            ok = prepare_quorum_bench(args.lab_dir)
        if not ok:
            print("ERROR: Could not prepare benchmark tool. Aborting.", file=sys.stderr)
            sys.exit(1)
    else:
        print("[prepare] --no-build: skipping tool preparation.")


    results: list[dict] = []

    if lab_type == "quorum":
        if not wait_for_quorum_ready(args.lab_dir):
            print("ERROR: Quorum contract deployment timed out. Aborting.", file=sys.stderr)
            sys.exit(1)
    elif lab_type == "cometbft":
        if not wait_for_comet_ready(args.lab_dir):
            print("ERROR: CometBFT initialization timed out. Aborting.", file=sys.stderr)
            sys.exit(1)

    benchmark_mode_enabled = (lab_type == "cometbft")
    if benchmark_mode_enabled:
        set_raw_throughput_mode(args.lab_dir, True)
    try:
        for step_idx, n in enumerate(args.steps):
            print(f"\n{'─' * 55}")
            print(f"  Step {step_idx + 1}/{len(args.steps)} — N = {n:,}")
            print(f"{'─' * 55}")

            start_wall = time.time()
            if lab_type == "cometbft":
                rpc_targets = [x.strip() for x in args.comet_rpc_targets.split(",") if x.strip()]
                baseline_metrics = get_comet_rpc_metrics(args.lab_dir, COMET_BENCH_NODE)
                if baseline_metrics is None:
                    print("ERROR: Failed to query CometBFT baseline metrics.", file=sys.stderr)
                    sys.exit(1)
                baseline = baseline_metrics["total_txs"]
                print(
                    f"  [pre] baseline total_txs={baseline}, "
                    f"unconfirmed_txs={baseline_metrics['unconfirmed_txs']}"
                )
                stats = run_cometbft_bench(
                    args.lab_dir,
                    n,
                    step=step_idx,
                    concurrency=args.concurrency,
                    targets=rpc_targets,
                )
                completion_ts, completion_metrics = wait_for_comet_completion(
                    args.lab_dir,
                    baseline_total_txs=baseline,
                    n=n,
                    timeout_s=600,
                    poll_s=2.0,
                )
                if completion_ts is None or completion_metrics is None:
                    print("ERROR: Timed out waiting for chain completion condition.", file=sys.stderr)
                    sys.exit(1)
                wall_time = completion_ts - start_wall
                committed = completion_metrics["total_txs"] - baseline
                tps = n / wall_time if wall_time > 0 else 0.0
                send_errors = int(stats.get("SendErrors", 0)) if stats else 0
                sent = int(stats.get("Sent", n)) if stats else n
                stats = {
                    "N": n,
                    "Sent": sent,
                    "SendErrors": send_errors,
                    "Transactions": committed,
                    "SuccessRate": (committed / n * 100.0) if n > 0 else 0.0,
                    "TotalTimeSeconds": wall_time,
                    "SentTime": float(stats.get("SentTime", 0.0)) if stats else 0.0,
                    "TPS": tps,
                    "BaselineTotalTxs": baseline,
                    "FinalTotalTxs": completion_metrics["total_txs"],
                    "FinalUnconfirmedTxs": completion_metrics["unconfirmed_txs"],
                }
            else:
                print(f"  [pre] Waiting for tx_count quiescence before baseline…")
                settled = wait_for_tx_quiescence(args.lab_dir, lab_type)
                print(f"  [pre] quiesced tx_count = {settled}")
                print("  [pre] Snapshotting tx_count baseline…")
                baseline = get_primary_tx_count(args.lab_dir, lab_type)
                print(f"  [pre] baseline = {baseline}")
                stats = run_quorum_bench(args.lab_dir, n)
                wall_time = time.time() - start_wall
                if stats is None:
                    print("  [warn] BENCH_STATS missing — computing from /tx_count delta.")
                    final = wait_for_tx_quiescence(args.lab_dir, lab_type)
                    committed = max(0, final - baseline)
                    tps = committed / wall_time if wall_time > 0 else 0.0
                    stats = {
                        "N": n, "Sent": n,
                        "SentTime": wall_time,
                        "Transactions": committed,
                        "SuccessRate": committed / n * 100 if n > 0 else 0,
                        "TotalTimeSeconds": wall_time,
                        "TPS": tps
                    }

                if stats is not None:
                    settled_final = wait_for_tx_quiescence(args.lab_dir, lab_type)
                    settled_committed = max(0, settled_final - baseline)
                    reported_committed = int(stats.get("Transactions", 0))
                    if settled_committed > reported_committed:
                        sent = int(stats.get("Sent", n))
                        stats["Transactions"] = settled_committed
                        stats["SuccessRate"] = (settled_committed / sent * 100) if sent > 0 else 0.0
                        print(
                            f"  [post] adjusted committed from {reported_committed} "
                            f"to {settled_committed} after settle."
                        )
                    if "SentTime" not in stats:
                        stats["SentTime"] = stats.get("TotalTimeSeconds", wall_time)
                    if wall_time > 0:
                        stats["TPS"] = stats["Transactions"] / wall_time

            stats["WallTimeSeconds"] = wall_time

            print(
                f"\n  ┌─ Result N={n:>7,} ───────────────────────────────\n"
                f"  │  Sent            : {stats['Sent']:>10,}\n"
                f"  │  Committed       : {stats['Transactions']:>10,}\n"
                f"  │  Success rate    : {stats['SuccessRate']:>9.1f} %\n"
                f"  │  Sent time       : {stats.get('SentTime', 0):>9.2f} s\n"
                f"  │  TPS             : {stats['TPS']:>10.1f}\n"
                f"  │  Wall time       : {wall_time:>9.2f} s\n"
                f"  └──────────────────────────────────────────────────"
            )

            results.append(stats)

            # Brief cooldown between steps to let the cluster settle.
            if step_idx < len(args.steps) - 1:
                cooldown = min(10, max(3, n // 5000))
                print(f"  [cooldown] sleeping {cooldown}s…")
                time.sleep(cooldown)
    finally:
        if benchmark_mode_enabled:
            set_raw_throughput_mode(args.lab_dir, False)
            set_benchmark_mode(args.lab_dir, False)


    data_file = os.path.join(res_dir, "blockchain_capacity.json")
    with open(data_file, "w") as f:
        json.dump(results, f, indent=4)
    print(f"\n  Raw data → {data_file}")




    print(f"\n{'=' * 55}")
    print(f"{'N':>10}  {'TPS':>10}  {'Success %':>10}  {'Wall s':>8}")
    print(f"{'─' * 55}")
    for r in results:
        print(
            f"{r['N']:>10,}  "
            f"{r['TPS']:>10.1f}  "
            f"{r['SuccessRate']:>9.1f}%  "
            f"{r.get('WallTimeSeconds', 0):>8.2f}"
        )
    print(f"{'=' * 55}\n")


if __name__ == "__main__":
    main()
