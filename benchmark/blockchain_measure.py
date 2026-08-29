# Script per la generazione di grafici dei tempi di risposta e simulazione attacchi.
# Esegue attacchi sequenziali e misura il tempo di reazione dell'intero sistema di difesa.
#
# Q1 methodology (paper §5.3.1): N isolated attacks are injected one at a time;
# for each attack the latency is the delta between the earliest IDS detection
# timestamp and the actuator mitigation timestamp (see measure_response_time.py).
# Results are stored as JSON and rendered as a boxplot per lab type.

import argparse, subprocess, time, os, json, re, sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent

# Keep matplotlib happy on read-only/odd HOME setups (Kathara hosts).
os.environ.setdefault("MPLCONFIGDIR", str(Path("/tmp/.mplconfig")))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def run_shell(cmd, timeout_s, label):
    try:
        wrapped_cmd = f"timeout --signal=KILL {timeout_s}s {cmd}"
        return subprocess.run(
            wrapped_cmd,
            shell=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        print(f"Warning: error while running {label}: {exc}")
        return None

def main():
    # Parsing argomenti
    parser = argparse.ArgumentParser(
        description="Sequential attack simulation + end-to-end response-time boxplot."
    )
    parser.add_argument("lab_dir", help="Percorso della directory del lab (es. ../lab/quorum)")
    parser.add_argument("N", type=int, nargs="?", default=10, help="Numero di attacchi da simulare (default: 10)")
    parser.add_argument("--cooldown", type=int, default=2, help="Secondi di attesa tra un attacco e l'altro")
    parser.add_argument("--attack-timeout", type=float, default=60.0,
                        help="Secondi massimi di attesa della mitigazione per ogni attacco (default: 60)")
    args = parser.parse_args()

    # Determina il tipo di lab dal percorso
    lab_type = "cometbft" if "cometbft" in args.lab_dir.lower() else "quorum"
    res_dir = f"result/{lab_type}"
    os.makedirs(res_dir, exist_ok=True)

    # Mappa nodo -> agent_id usato dall'alert_forwarder di quell'IDS
    # (deve corrispondere al campo 'ids' che i forwarder reali usano nelle POST)
    agent_map = {
        "light0": "snort",
        "light1": "suricata",
        "light2": "zeek",
    } if lab_type == "cometbft" else {
        "member0": "snort",
        "member1": "suricata",
        "member2": "zeek",
    }

    def reset_dedup(lab_dir, nodes, agent_map):
        """Invia SAFE_ENVIRONMENT con il corretto agent_id a ogni nodo per pulire seen_types."""
        for node in nodes:
            agent = agent_map.get(node, node)
            payload = json.dumps([{"ids": agent, "type": "SAFE_ENVIRONMENT", "value": 0}])
            cmd_reset = (
                f"kathara exec -d {lab_dir} {node} -- "
                f"curl -s --connect-timeout 2 --max-time 2 -X POST "
                f"-H 'Content-Type: application/json' "
                f"-d '{payload}' http://localhost:3000/alert"
            )
            run_shell(cmd_reset, timeout_s=5, label=f"reset {node}")

    print("Resetting blockchain node state to SAFE_ENVIRONMENT before simulating...")
    reset_nodes = ["light0", "light1", "light2"] if lab_type == "cometbft" else ["member0", "member1", "member2"]
    reset_dedup(args.lab_dir, reset_nodes, agent_map)
    time.sleep(2)

    deltas = []
    failed = []

    print(f"Avvio simulazione sequenziale di {args.N} attacchi su {lab_type}...")

    measure_script = SCRIPT_DIR / "measure_response_time.py"

    for i in range(1, args.N + 1):
        print(f"\n--- Attacco {i}/{args.N} ---")
        # Salva il tempo di inizio
        start_ts = time.time()

        attack_cmd = (
            f"kathara exec -d {args.lab_dir} attacker -- "
            "curl -s --connect-timeout 2 --max-time 5 -o /dev/null "
            "'http://10.0.0.80:3000/rest/products/search?q=1=1'"
        )
        attack_out = run_shell(attack_cmd, timeout_s=12, label="attacker request")
        if attack_out and attack_out.returncode != 0:
            err = (attack_out.stderr or "").strip()
            if err:
                print(f"Warning: attacker request failed: {err}")

        # Polling per attendere la reazione del sistema (con deadline fissa)
        delta_val = None
        out = None
        deadline_ts = time.time() + args.attack_timeout

        while time.time() < deadline_ts:
            time.sleep(0.05)  # Attesa per permettere a IDS -> Blockchain -> Actuator di elaborare

            cmd = f"{sys.executable} {measure_script} {args.lab_dir} --since {start_ts}"
            try:
                out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                continue

            m = re.search(r'Delta:\s*([0-9\.]+)s', out.stdout)
            if m:
                delta_val = float(m.group(1))
                break # Misura trovata, esco dal polling

        if delta_val is not None:
            deltas.append(delta_val)
            print(f"Attacco {i} misurato con successo. Delta: {delta_val:.4f}s")
        else:
            failed.append(i)
            print(f"Attacco {i} fallito o non rilevato in tempo dal sistema di difesa "
                  f"(timeout {args.attack_timeout:.0f}s).")
            if out is not None and out.stderr.strip():
                print(f"Dettaglio errore misura (ult. tentativo): {out.stderr.strip()}")

        # Reset dedup tra un attacco e l'altro: svuota seen_types per tutti gli agenti
        # così il prossimo attacco SQL viene processato come nuovo (non bloccato dalla dedup).
        if i < args.N:
            reset_dedup(args.lab_dir, reset_nodes, agent_map)
            time.sleep(args.cooldown)


    if not deltas:
        print("\nWarning: nessun dato rilevato nel range temporale complessivo. Salvo comunque un file vuoto senza interrompere la suite.")

    # Salvataggio dati json
    dfile = os.path.join(res_dir, f"data_N{args.N}.json")
    with open(dfile, "w") as f:
        json.dump(deltas, f, indent=4)

    print(f"\nGenerati con successo: {dfile}")

    # Riepilogo statistico
    if deltas:
        srt = sorted(deltas)
        mean = sum(srt) / len(srt)
        median = srt[len(srt) // 2] if len(srt) % 2 else (srt[len(srt)//2 - 1] + srt[len(srt)//2]) / 2
        p95 = srt[min(len(srt) - 1, int(round(0.95 * len(srt))) - 1)] if len(srt) > 1 else srt[0]
        print(f"\n  Risultati ({lab_type}, N={args.N}):")
        print(f"    successi : {len(deltas)}/{args.N}"
              + (f"   (falliti: {failed})" if failed else ""))
        print(f"    media    : {mean:.4f}s")
        print(f"    mediana  : {median:.4f}s")
        print(f"    min/max  : {srt[0]:.4f}s / {srt[-1]:.4f}s")
        print(f"    p95      : {p95:.4f}s")

    # ── Boxplot (Figura 4 del paper) ─────────────────────────────────────────
    if deltas:
        tech_label = "CometBFT" if lab_type == "cometbft" else "Quorum"
        label = f"{tech_label} (N={args.N})\nMean: {sum(deltas)/len(deltas):.4f}s"
        fig, ax = plt.subplots(figsize=(8, 5))
        bplot = ax.boxplot(
            [deltas],
            patch_artist=True,
            tick_labels=[label],
            widths=0.25,
            medianprops=dict(color="#333333", linewidth=2),
            whiskerprops=dict(linewidth=1, color="#333333"),
            capprops=dict(linewidth=1, color="#333333"),
        )
        bplot["boxes"][0].set_facecolor("#8DCB9E" if lab_type == "cometbft" else "#8CA8D1")
        ax.set_ylabel("Response Time (seconds)")
        ax.set_xlabel("Technology")
        ax.set_title(f"End-to-end response time — {tech_label}")
        plt.tight_layout()
        for ext in ("png", "pdf"):
            out_path = os.path.join(res_dir, f"response_time_N{args.N}.{ext}")
            fig.savefig(out_path, dpi=200)
        plt.close(fig)
        print(f"Boxplot salvato in: {res_dir}/response_time_N{args.N}.png|pdf")

if __name__ == "__main__":
    main()
