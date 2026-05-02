"""
benchmark_capacity.py — Test della capacità del loop completo SPS (solo CometBFT).

Misura le seguenti metriche per ogni step N:

  N           : Ordine di grandezza degli attacchi da inviare (parametro)
  Sent        : Attacchi realmente spediti dal nodo attaccante
  Detected    : Attacchi rilevati dall'IDS che ne ha trovati di più (max fra i 3)
  Ingress     : Somma dei totalAlertsReceived dei 3 nodi (light0 + light1 + light2)
  Sensitive   : Numero di alert ritenuti validi dalla deduplicazione API
                (somma di totalAlertsProcessed dei 3 nodi)
  Transactions: Numero di transazioni committate sulla blockchain (delta tx_count)

Comportamento atteso con deduplicazione attiva e alert negativi disabilitati:
  - attacker_burst.py cicla sui 4 tipi di attacco (SQL, XSS, PATH, CMD)
  - ogni IDS rileva tutti e 4 i tipi e li inoltra al proprio light node
  - la deduplicazione API scarta alert identici allo stato già confermato
  - per ogni step: max transazioni = N_IDS × N_tipi_diversi = 3 × 4 = 12
  - al reset (SAFE_ENVIRONMENT) la cache di dedup viene azzerata
  - gli alert negativi sono soppressi tramite il marker disable_negative_alerts

NON toccare blockchain_benchmark.py: quel test usa raw throughput (dedup disabilitata,
negative alert soppressi) e non deve essere influenzato da questo script.
"""

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

# ── Topologia CometBFT ───────────────────────────────────────────────────────
# I 3 nodi "light" sono gli agent node: ciascuno è accoppiato a un IDS.
#   light0 ← ids_snort   (172.16.1.10)
#   light1 ← ids_suricata (172.16.2.10)
#   light2 ← ids_zeek    (172.16.3.10)
LIGHT_NODES   = ["light0", "light1", "light2"]
IDS_NODES     = ["ids_snort", "ids_suricata", "ids_zeek"]
# Tutti i nodi con sps-node: dedup va impostata su tutti per evitare split-brain
ALL_SPS_NODES = ["validator0", "validator1", "validator2",
                 "light0", "light1", "light2", "fullnode0"]

N_IDS          = len(LIGHT_NODES)          # 3
ATTACK_TYPES   = 4                         # SQL_INJECTION, XSS_ATTACK, PATH_TRAVERSAL, COMMAND_INJECTION
EXPECTED_TX    = N_IDS * ATTACK_TYPES


# ── Utility ──────────────────────────────────────────────────────────────────

def run_cmd(cmd: str, timeout: int = 120) -> str:
    """Esegue un comando shell, restituisce stdout (stringa vuota in caso di errore)."""
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
        if res.returncode != 0 and res.stderr:
            stderr = res.stderr.decode('utf-8', errors='replace').strip()
            print(f"  [cmd err] {stderr[:200]}", file=sys.stderr)
        return res.stdout.decode('utf-8', errors='replace').strip()
    except subprocess.TimeoutExpired:
        print(f"  [cmd timeout] {cmd[:80]}", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"  [cmd exception] {e}", file=sys.stderr)
        return ""


def kathara_exec(lab_dir: str, node: str, inner: str, timeout: int = 60) -> str:
    return run_cmd(f"kathara exec -d {lab_dir} {node} -- {inner}", timeout=timeout)


# ── Controllo connettività ───────────────────────────────────────────────────

def check_connectivity(lab_dir: str) -> bool:
    """Verifica che l'attaccante possa raggiungere juice_shop."""
    print("  [check] Verifica connettività attaccante → juice_shop (10.0.0.80:3000)...")
    code = kathara_exec(lab_dir, "attacker",
                        "curl -s -o /dev/null -w '%{http_code}' http://10.0.0.80:3000",
                        timeout=15)
    print(f"  [check] HTTP {code}")
    if code == "200":
        return True
    if code == "000":
        print("  [WARN] Connessione rifiutata — gli IDS non rileveranno nulla!")
    else:
        print(f"  [WARN] HTTP {code} — i risultati potrebbero essere parziali.")
    return False


# ── Marker / configurazione runtime ─────────────────────────────────────────

def set_negative_alerts(lab_dir: str, disabled: bool):
    """
    Crea/rimuove il marker /shared/disable_negative_alerts.
    Con il marker presente alert_forwarder.py sopprime gli alert di recovery
    (value=0), evitando che la dedup venga azzerata automaticamente dopo ogni
    singola rilevazione — comportamento necessario per questo test.
    """
    marker = os.path.abspath(os.path.join(lab_dir, "shared", "disable_negative_alerts"))
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    if disabled:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("1\n")
        print("  [config] Negative alerts DISABILITATI (marker creato)")
    else:
        if os.path.exists(marker):
            os.remove(marker)
        print("  [config] Negative alerts ABILITATI (marker rimosso)")


def set_dedup(lab_dir: str, enabled: bool):
    """
    Imposta lo stato della deduplicazione ledger su TUTTI i nodi sps-node.
    Usa lo stesso set di nodi di blockchain_benchmark.py per evitare split-brain.
    """
    enabled_str = "true" if enabled else "false"
    print(f"  [config] Ledger deduplication → {enabled} su tutti i nodi sps-node...")
    for node in ALL_SPS_NODES:
        out = kathara_exec(
            lab_dir, node,
            f"curl -s -X POST 'http://127.0.0.1:3000/config/dedup?enabled={enabled_str}'",
            timeout=10
        )
        if out:
            print(f"    {node}: {out}")


# ── Lettura metriche IDS (Detected) ─────────────────────────────────────────

def get_ids_log_counts(lab_dir: str) -> dict[str, int]:
    """
    Legge il numero di righe di log da ciascun IDS.
    Restituisce un dict {node_name: count}.
    Le righe commentate di Zeek (che iniziano con #) sono escluse.
    """
    counts: dict[str, int] = {}

    raw = kathara_exec(
        lab_dir, "ids_snort",
        "sh -c 'cat /var/log/snort/alert_fast.txt 2>/dev/null | wc -l'",
        timeout=15
    )
    counts["ids_snort"] = int(raw or 0)

    raw = kathara_exec(
        lab_dir, "ids_suricata",
        "sh -c 'cat /var/log/suricata/fast.log 2>/dev/null | wc -l'",
        timeout=15
    )
    counts["ids_suricata"] = int(raw or 0)

    raw = kathara_exec(
        lab_dir, "ids_zeek",
        "sh -c \"grep -v '^#' /var/log/zeek/signatures.log 2>/dev/null | wc -l\"",
        timeout=15
    )
    counts["ids_zeek"] = int(raw or 0)

    return counts


# ── Lettura metriche blockchain (Ingress / Sensitive / Transactions) ─────────

def get_node_stats(lab_dir: str, node: str) -> tuple[int, int]:
    """
    Legge totalAlertsReceived e totalAlertsProcessed da /stats di un singolo nodo.
    Restituisce (received, processed).
    """
    raw = kathara_exec(lab_dir, node, "curl -s --max-time 5 http://127.0.0.1:3000/stats", timeout=10)
    if not raw:
        return 0, 0
    try:
        data = json.loads(raw)
        return int(data.get("totalAlertsReceived", 0)), int(data.get("totalAlertsProcessed", 0))
    except Exception:
        return 0, 0


def get_cluster_stats(lab_dir: str) -> tuple[int, int]:
    """
    Somma totalAlertsReceived e totalAlertsProcessed su tutti e 3 i light node.
    Ingress  = somma received  (quanti alert sono arrivati all'API di ciascun nodo)
    Sensitive = somma processed (quanti alert hanno superato la deduplicazione)
    """
    total_recv = 0
    total_proc = 0
    for node in LIGHT_NODES:
        recv, proc = get_node_stats(lab_dir, node)
        total_recv += recv
        total_proc += proc
    return total_recv, total_proc


def get_tx_count(lab_dir: str) -> int:
    """
    Legge il numero di transazioni committate da light0 (/tx_count).
    CometBFT ha un ledger condiviso via consenso, quindi un nodo solo è sufficiente.
    """
    raw = kathara_exec(
        lab_dir, LIGHT_NODES[0],
        "curl -s --max-time 5 http://127.0.0.1:3000/tx_count",
        timeout=10
    )
    if not raw:
        return 0
    try:
        return int(json.loads(raw).get("count", 0))
    except Exception:
        return 0


# ── Reset dello stato ────────────────────────────────────────────────────────

def reset_state(lab_dir: str):
    """
    Invia SAFE_ENVIRONMENT a tutti e 3 i light node per:
    1. Azzerare il vettore di stato del ledger (tutti i parametri → 0)
    2. Pulire la cache last_voted di ogni nodo (dedup reset per il prossimo step)

    Il payload usa value=0 (semanticamente: "nessun attacco") con type=SAFE_ENVIRONMENT.
    api.rs lo tratta come reset incondizionato indipendentemente dal value.
    """
    print("  [reset] Invio SAFE_ENVIRONMENT a tutti i light node...")
    agent_names = ["snort", "suricata", "zeek"]
    for node, agent in zip(LIGHT_NODES, agent_names):
        payload = json.dumps([{"ids": agent, "type": "SAFE_ENVIRONMENT", "value": 0}])
        out = kathara_exec(
            lab_dir, node,
            f"curl -s -X POST -H 'Content-Type: application/json' "
            f"-d '{payload}' http://127.0.0.1:3000/alert",
            timeout=15
        )
        print(f"    {node} ({agent}): {out or '(no response)'}")
    print("  [reset] Attesa 3s per propagazione stato SAFE...")
    time.sleep(3)


# ── Attesa quiescenza ────────────────────────────────────────────────────────

def wait_for_ingress_settle(
    lab_dir: str,
    baseline_recv: int,
    settle_time: int,
    poll_s: float = 3.0,
    stable_cycles: int = 3,
) -> tuple[int, int]:
    """
    Aspetta che il numero di alert ricevuti smetta di crescere.

    Non si aspetta più `n * N_IDS` (irraggiungibile per IDS imprecisi), ma usa
    una politica di quiescenza: se il valore non cresce per `stable_cycles`
    poll consecutivi si considera stabile.

    Restituisce (final_recv, final_proc).
    """
    print(f"  [wait] Attesa {settle_time}s iniziale per processing...")
    time.sleep(settle_time)

    last_recv, _ = get_cluster_stats(lab_dir)
    stable = 0

    print("  [wait] Polling quiescenza ingress...")
    while stable < stable_cycles:
        time.sleep(poll_s)
        curr_recv, _ = get_cluster_stats(lab_dir)
        delta = curr_recv - baseline_recv
        print(f"  [wait] Ingress delta={delta}, stable_cycles={stable}/{stable_cycles}")
        if curr_recv == last_recv:
            stable += 1
        else:
            stable = 0
            last_recv = curr_recv

    # Drain finale: attende ancora 2 cicli dopo la stabilizzazione
    for _ in range(2):
        time.sleep(poll_s)
        new_recv, _ = get_cluster_stats(lab_dir)
        if new_recv > last_recv:
            last_recv = new_recv
            stable = 0
            # se riparte, riaspetta
            while stable < stable_cycles:
                time.sleep(poll_s)
                curr_recv, _ = get_cluster_stats(lab_dir)
                if curr_recv == last_recv:
                    stable += 1
                else:
                    stable = 0
                    last_recv = curr_recv

    final_recv, final_proc = get_cluster_stats(lab_dir)
    return final_recv, final_proc


def wait_for_tx_quiescence(
    lab_dir: str,
    poll_s: float = 2.0,
    stable_cycles: int = 4,
    max_wait_s: int = 60,
) -> int:
    """
    Aspetta che tx_count smetta di crescere (consenso completato).
    Restituisce il valore finale stabile.
    """
    last = get_tx_count(lab_dir)
    stable = 0
    deadline = time.time() + max_wait_s
    while time.time() < deadline and stable < stable_cycles:
        time.sleep(poll_s)
        curr = get_tx_count(lab_dir)
        if curr == last:
            stable += 1
        else:
            stable = 0
            last = curr
    return get_tx_count(lab_dir)


# ── Sync attacker_burst.py ───────────────────────────────────────────────────

def sync_attacker_script(lab_dir: str):
    """Propaga attacker_burst.py nella cartella shared del lab."""
    host_script = SCRIPT_DIR / "attacker_burst.py"
    if not host_script.exists():
        alt = Path.cwd() / "attacker_burst.py"
        if alt.exists():
            host_script = alt
        else:
            print(f"  [WARN] attacker_burst.py non trovato in {SCRIPT_DIR} né in {Path.cwd()}")
            return

    dest = os.path.join(lab_dir, "shared", "attacker_burst.py")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.samefile(str(host_script), dest):
        print(f"  [sync] attacker_burst.py già in shared/")
        return
    with open(host_script, "rb") as f_in, open(dest, "wb") as f_out:
        f_out.write(f_in.read())
    print(f"  [sync] {host_script} → {dest}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark del loop completo SPS (solo CometBFT) con deduplicazione attiva."
    )
    parser.add_argument("lab_dir", help="Path alla directory Kathara del lab cometbft")
    parser.add_argument(
        "--steps", nargs="+", type=int,
        default=[10, 100, 1000, 5000, 10000, 20000, 50000],
        help="Lista di N attacchi da testare per ogni step."
    )
    parser.add_argument(
        "--settle-time", type=int, default=10,
        help="Attesa iniziale in secondi dopo il burst prima di iniziare il polling (default: 10)."
    )
    args = parser.parse_args()

    if "cometbft" not in args.lab_dir.lower():
        print("ERRORE: questo test è progettato solo per il lab CometBFT.", file=sys.stderr)
        sys.exit(1)

    res_dir = "result/cometbft"
    os.makedirs(res_dir, exist_ok=True)

    print("=" * 65)
    print("  SPS Capacity Benchmark — CometBFT (deduplicazione attiva)")
    print("=" * 65)
    print(f"  Lab dir    : {args.lab_dir}")
    print(f"  Steps      : {args.steps}")
    print(f"  Settle time: {args.settle_time}s")
    print(f"  Risultati  : {res_dir}/")
    print(f"  IDS nodes  : {IDS_NODES}")
    print(f"  Light nodes: {LIGHT_NODES}")
    print(f"  Max tx/step: {EXPECTED_TX} (= {N_IDS} IDS × {ATTACK_TYPES} tipi)")
    print("=" * 65)

    sync_attacker_script(args.lab_dir)
    check_connectivity(args.lab_dir)

    # ── Setup: abilita negative alert suppression + deduplicazione ───────────
    # Importante: NON disabilitiamo la dedup (al contrario di blockchain_benchmark.py).
    # Questo test vuole misurare proprio la dedup in azione.
    set_negative_alerts(args.lab_dir, disabled=True)
    set_dedup(args.lab_dir, enabled=True)

    results: list[dict] = []

    try:
        for step_idx, n in enumerate(args.steps):
            print(f"\n{'─' * 65}")
            print(f"  Step {step_idx + 1}/{len(args.steps)} — N = {n:,}")
            print(f"{'─' * 65}")

            # ── Baseline ─────────────────────────────────────────────────────
            baseline_ids   = get_ids_log_counts(args.lab_dir)
            baseline_recv, baseline_proc = get_cluster_stats(args.lab_dir)
            baseline_tx    = wait_for_tx_quiescence(args.lab_dir)
            print(f"  [baseline] IDS={baseline_ids}, Recv={baseline_recv}, "
                  f"Proc={baseline_proc}, Tx={baseline_tx}")

            # ── Burst ─────────────────────────────────────────────────────────
            # Usa --pattern cycle: garantisce che tutti e 4 i tipi di attacco
            # vengano inviati in modo round-robin, massimizzando i tipi distinti
            # rilevati dagli IDS e quindi le transazioni uniche generate.
            print(f"  [burst] Invio {n:,} attacchi (pattern cycle)...")
            burst_out = kathara_exec(
                args.lab_dir,
                "attacker",
                f"python3 -u /shared/attacker_burst.py {n} --pattern cycle",
                timeout=max(120, n // 100 + 60)
            )
            # Estrai il numero reale di attacchi inviati dall'output del burst
            sent = n
            for line in (burst_out or "").splitlines():
                if "Firing" in line and "attacks" in line:
                    try:
                        sent = int(line.split()[1].replace(",", ""))
                    except Exception:
                        pass

            print(f"  [burst] Completato. Sent≈{sent}")
            if burst_out:
                # Mostra solo le ultime 5 righe dell'output per non spammare
                lines = burst_out.strip().splitlines()
                for l in lines[-5:]:
                    print(f"    {l}")

            # ── Polling quiescenza ingress ───────────────────────────────────
            final_recv, final_proc = wait_for_ingress_settle(
                args.lab_dir,
                baseline_recv=baseline_recv,
                settle_time=args.settle_time,
            )

            # ── Attesa quiescenza tx (consenso) ─────────────────────────────
            print("  [wait] Attesa quiescenza tx_count (consenso CometBFT)...")
            final_tx = wait_for_tx_quiescence(args.lab_dir)

            # ── Lettura IDS log counts ───────────────────────────────────────
            final_ids = get_ids_log_counts(args.lab_dir)

            # ── Calcolo delta ────────────────────────────────────────────────
            # Detected: IDS con il maggior numero di rilevazioni (delta su baseline)
            delta_ids = {k: final_ids[k] - baseline_ids.get(k, 0) for k in final_ids}
            detected = max(delta_ids.values()) if delta_ids else 0

            # Ingress: somma degli alert ricevuti da tutti e 3 i light node
            ingress = final_recv - baseline_recv

            # Sensitive: somma degli alert che hanno superato la deduplicazione
            sensitive = final_proc - baseline_proc

            # Transactions: delta tx committate
            transactions = final_tx - baseline_tx

            print(
                f"\n  ┌─ Risultati N={n:>8,} ──────────────────────────────\n"
                f"  │  Sent        : {sent:>10,}  (attacchi effettivamente inviati)\n"
                f"  │  Detected    : {detected:>10,}  (max rilevazioni IDS singolo)\n"
                f"  │  Ingress     : {ingress:>10,}  (somma alert ricevuti dai 3 nodi)\n"
                f"  │  Sensitive   : {sensitive:>10,}  (alert che superano la dedup)\n"
                f"  │  Transactions: {transactions:>10,}  (tx committate, atteso ≤ {EXPECTED_TX})\n"
                f"  │  IDS detail  : snort={delta_ids.get('ids_snort',0)} "
                f"suricata={delta_ids.get('ids_suricata',0)} "
                f"zeek={delta_ids.get('ids_zeek',0)}\n"
                f"  └──────────────────────────────────────────────────────"
            )

            results.append({
                "N":            n,
                "Sent":         sent,
                "Detected":     detected,
                "Ingress":      ingress,
                "Sensitive":    sensitive,
                "Transactions": transactions,
                "IDSDetail":    delta_ids,
            })

            # ── Reset per il prossimo step ───────────────────────────────────
            if step_idx < len(args.steps) - 1:
                reset_state(args.lab_dir)

    finally:
        # ── Cleanup: ripristina lo stato normale del sistema ─────────────────
        print("\n[cleanup] Ripristino configurazione normale...")
        set_negative_alerts(args.lab_dir, disabled=False)
        # Non tocchiamo la dedup: resta abilitata come da configurazione di default.
        # blockchain_benchmark.py la disabilita esplicitamente all'avvio e la
        # riabilita al termine — questo script non deve interferire con quel test.

    # ── Salvataggio risultati ────────────────────────────────────────────────
    data_file = os.path.join(res_dir, "capacity_results.json")
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4)
    print(f"\n  Dati salvati in: {data_file}")

    # ── Tabella riepilogativa ────────────────────────────────────────────────
    print(f"\n{'=' * 75}")
    print(f"{'N':>10}  {'Sent':>8}  {'Detected':>9}  {'Ingress':>8}  "
          f"{'Sensitive':>10}  {'Tx':>6}")
    print(f"{'─' * 75}")
    for r in results:
        print(
            f"{r['N']:>10,}  {r['Sent']:>8,}  {r['Detected']:>9,}  "
            f"{r['Ingress']:>8,}  {r['Sensitive']:>10,}  {r['Transactions']:>6,}"
        )
    print(f"{'=' * 75}")
    print(f"  Nota: Transactions ottimale = {EXPECTED_TX} "
          f"({N_IDS} IDS × {ATTACK_TYPES} tipi distinti per step)")


if __name__ == "__main__":
    main()