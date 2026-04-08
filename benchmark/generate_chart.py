# Script per la generazione di grafici dei tempi di risposta e simulazione attacchi.
# Esegue attacchi sequenziali e misura il tempo di reazione dell'intero sistema di difesa.

import argparse, subprocess, time, os, json, re, sys
import matplotlib.pyplot as plt

def main():
    # Parsing argomenti
    parser = argparse.ArgumentParser()
    parser.add_argument("lab_dir", help="Percorso della directory del lab (es. ../lab/quorum)")
    parser.add_argument("N", type=int, nargs="?", default=10, help="Numero di attacchi da simulare (default: 10)")
    # MODIFICA 1: Cooldown di default portato a 5 secondi per isolare bene i blocchi CometBFT
    parser.add_argument("--cooldown", type=int, default=5, help="Secondi di attesa tra un attacco e l'altro")
    args = parser.parse_args()

    # Determina il tipo di lab dal percorso
    lab_type = "cometbft" if "cometbft" in args.lab_dir.lower() else "quorum"
    res_dir = f"result/{lab_type}"
    os.makedirs(res_dir, exist_ok=True)

    deltas = []

    print(f"Avvio simulazione sequenziale di {args.N} attacchi su {lab_type}...")
    
    for i in range(1, args.N + 1):
        print(f"\n--- Attacco {i}/{args.N} ---")
        # Salva il tempo di inizio
        start_ts = time.time()
        
        subprocess.run(
            f"kathara exec -d {args.lab_dir} attacker -- curl -s -o /dev/null 'http://10.0.0.80:3000/rest/products/search?q=1=1'",
            shell=True
        )
        
        # Polling per attendere la reazione del sistema
        # MODIFICA 2: Portato da 15 a 60 (60 * 0.5s = 30 secondi di pazienza massima prima di dare "fallito")
        max_retries = 60
        delta_val = None
        
        for attempt in range(max_retries):
            time.sleep(0.5) # Attesa per permettere a IDS -> Blockchain -> Actuator di elaborare
            
            cmd = f"{sys.executable} measure_response_time.py {args.lab_dir} --since {start_ts}"
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
        
        # Attesa prima del prossimo attacco per isolare gli eventi
        if i < args.N:
            print(f"Attesa di {args.cooldown}s di cooldown prima del prossimo attacco...")
            time.sleep(args.cooldown)

    if not deltas:
        print("\nErrore: nessun dato rilevato (Nessun attacco captato nel range temporale complessivo).")
        sys.exit(1)

    # Salvataggio dati json
    dfile = os.path.join(res_dir, f"data_N{args.N}.json")
    with open(dfile, "w") as f:
        json.dump(deltas, f, indent=4)

    # Creazione Boxplot
    plt.figure()
    plt.boxplot(deltas)
    plt.title(f"Response Time - {args.N} attacchi ({lab_type})")
    plt.ylabel("Tempo di risposta (s)")
    plt.grid(True, linestyle='--', alpha=0.7)
    
    # Salva grafico in png
    cfile = os.path.join(res_dir, f"chart_N{args.N}.png")
    plt.savefig(cfile)
    print(f"\nGenerati con successo: {dfile} e {cfile}")

if __name__ == "__main__":
    main()