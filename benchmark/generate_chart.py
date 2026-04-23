# Script per la generazione di grafici dei tempi di risposta e simulazione attacchi.
# Esegue attacchi sequenziali e misura il tempo di reazione dell'intero sistema di difesa.

import argparse, subprocess, time, os, json, re, sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


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
    parser = argparse.ArgumentParser()
    parser.add_argument("lab_dir", help="Percorso della directory del lab (es. ../lab/quorum)")
    parser.add_argument("N", type=int, nargs="?", default=10, help="Numero di attacchi da simulare (default: 10)")
    parser.add_argument("--cooldown", type=int, default=2, help="Secondi di attesa tra un attacco e l'altro")
    args = parser.parse_args()

    # Determina il tipo di lab dal percorso
    lab_type = "cometbft" if "cometbft" in args.lab_dir.lower() else "quorum"
    res_dir = f"result/{lab_type}"
    os.makedirs(res_dir, exist_ok=True)
    
    # Inietta SAFE_ENVIRONMENT prima della run per esser sicuri di azzerare i blocchi dedup in sospeso
    # eventualmente lasciati in canna dalle run precedenti (ex. blockchain_benchmark)
    print("Resetting blockchain node state to SAFE_ENVIRONMENT prima di simulare...")
    reset_node = "light0" if lab_type == "cometbft" else "member0"
    cmd_reset = (
        f"kathara exec -d {args.lab_dir} {reset_node} -- "
        "curl -s --connect-timeout 2 --max-time 5 -X POST -H 'Content-Type: application/json' "
        "-d '[{\"type\": \"SAFE_ENVIRONMENT\", \"value\": 1}]' http://localhost:3000/alert"
    )
    reset_out = run_shell(cmd_reset, timeout_s=12, label="SAFE_ENVIRONMENT reset")
    if reset_out and reset_out.returncode != 0:
        err = (reset_out.stderr or "").strip()
        if err:
            print(f"Warning: reset command failed: {err}")
    time.sleep(2)

    deltas = []

    print(f"Avvio simulazione sequenziale di {args.N} attacchi su {lab_type}...")
    
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
        
        # Polling per attendere la reazione del sistema
        # MODIFICA 2: Portato da 15 a 60 (60 * 0.5s = 30 secondi di pazienza massima prima di dare "fallito")
        max_retries = 60
        delta_val = None
        
        for attempt in range(max_retries):
            time.sleep(0.5) # Attesa per permettere a IDS -> Blockchain -> Actuator di elaborare
            
            measure_script = SCRIPT_DIR / "measure_response_time.py"
            cmd = f"{sys.executable} {measure_script} {args.lab_dir} --since {start_ts}"
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            m = re.search(r'Delta:\s*([0-9\.]+)s', out.stdout)
            if m:
                delta_val = float(m.group(1))
                break # Misura trovata, esco dal polling
                
        if delta_val is not None:
            deltas.append(delta_val)
            print(f"Attacco {i} misurato con successo. Delta: {delta_val:.4f}s")
        else:
            print(f"Attacco {i} fallito o non rilevato in tempo dal sistema di difesa.")
            if out.stderr.strip():
                print(f"Dettaglio errore misura (ult. tentativo): {out.stderr.strip()}")
            
        if i < args.N:
            time.sleep(args.cooldown)

    if not deltas:
        print("\nWarning: nessun dato rilevato nel range temporale complessivo. Salvo comunque un file vuoto senza interrompere la suite.")

    # Salvataggio dati json
    dfile = os.path.join(res_dir, f"data_N{args.N}.json")
    with open(dfile, "w") as f:
        json.dump(deltas, f, indent=4)

    print(f"\nGenerati con successo: {dfile}")

if __name__ == "__main__":
    main()