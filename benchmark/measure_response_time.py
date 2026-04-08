# Script per misurare il tempo di risposta del sistema (Detection IDS -> Mitigation Actuator).
# Analizza i log di Snort, Suricata, Zeek e Actuator per calcolare il delta temporale.

import re, argparse, subprocess, sys, os
import io
from datetime import datetime, timezone

# Ensure UTF-8 environment
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['LC_ALL'] = 'C.UTF-8'
os.environ['LANG'] = 'C.UTF-8'

if sys.stdout.encoding is None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding is None:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def run_cmd(cmd):
    """Esegue un comando shell in modo sincrono e restituisce l'output."""
    res = subprocess.run(cmd, shell=True, capture_output=True)
    return res.stdout.decode('utf-8', errors='replace')

def parse_ids_time(cmd, regex, time_fmt, since):
    """Estrae i timestamp dai log testuali di Snort o Suricata."""
    out = run_cmd(cmd)
    timestamps = []
    for line in out.strip().split('\n'):
        if not line.strip(): continue
        m = re.search(regex, line)
        if m:
            ts_str = m.group(1)
            try:
                # Gestione dell'anno mancante nei log di Snort
                if ts_str.count('/') == 1 and '%y' in time_fmt:
                    ts_str = f"{datetime.now().year % 100:02d}/{ts_str}"
                elif ts_str.count('/') == 1 and '%Y' in time_fmt:
                    ts_str = f"{datetime.now().year}/{ts_str}"
                
                ts = datetime.strptime(ts_str, time_fmt).replace(tzinfo=timezone.utc).timestamp()
                if ts >= since:
                    timestamps.append(ts)
            except Exception: 
                pass
    return timestamps

def get_zeek_time(lab_dir, since):
    """Estrae i timestamp UNIX dai log di Zeek."""
    out = run_cmd(f"kathara exec -d {lab_dir} ids_zeek -- cat /var/log/zeek/signatures.log")
    timestamps = []
    for line in out.strip().split('\n'):
        if line.startswith('#') or not line.strip(): continue
        p = line.split('\t')
        if len(p) >= 6:
            try:
                ts = float(p[0])
                if ts >= since: 
                    timestamps.append(ts)
            except ValueError: 
                pass
    return timestamps

def get_actuator_time(lab_dir, since):
    """Estrae i timestamp in cui l'actuator ha ricevuto l'azione di mitigazione."""
    out = run_cmd(f"kathara exec -d {lab_dir} actuator -- cat /var/log/actuator_actions.log")
    timestamps = []
    for line in out.strip().split('\n'):
        if "RECEIVED action:" in line:
            m = re.search(r'^\[(.*?)\]', line)
            if m:
                ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                if ts >= since: 
                    timestamps.append(ts)
    return timestamps

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lab_dir", nargs="?", default="../lab/quorum")
    parser.add_argument("--since", type=float, default=0.0)
    args = parser.parse_args()

    # 1. Trova le detection (Snort, Suricata, Zeek)
    snort_ts = parse_ids_time(
        f"kathara exec -d {args.lab_dir} ids_snort -- cat /var/log/snort/alert_fast.txt", 
        r'^(\d{2}/\d{2}(?:/\d{2})?-\d{2}:\d{2}:\d{2}\.\d+)', 
        "%y/%m/%d-%H:%M:%S.%f", args.since
    )
    suricata_ts = parse_ids_time(
        f"kathara exec -d {args.lab_dir} ids_suricata -- cat /var/log/suricata/fast.log",
        r'^(\d{2}/\d{2}/\d{4}-\d{2}:\d{2}:\d{2}\.\d+)', 
        "%m/%d/%Y-%H:%M:%S.%f", args.since
    )
    zeek_ts = get_zeek_time(args.lab_dir, args.since)
    
    all_ids_ts = snort_ts + suricata_ts + zeek_ts
    if not all_ids_ts:
        # Nessuna detection trovata, uscita silenziosa così il main file può riprovare
        sys.exit(1)
        
    # Prende la primissima detection registrata da uno qualsiasi degli IDS per questo attacco
    t_detect = min(all_ids_ts)
    
    # 2. Trova la mitigazione sull'Actuator
    actuator_ts = get_actuator_time(args.lab_dir, args.since)
    valid_mitigations = [t for t in actuator_ts if t >= t_detect]
    
    if not valid_mitigations:
        sys.exit(1)
        
    # Prende la prima azione dell'actuator scatenata subito dopo la detection
    t_mitigate = min(valid_mitigations)
    
    # 3. Calcolo del delta isolato
    delta = t_mitigate - t_detect
    print(f"Delta: {delta:.4f}s")

if __name__ == '__main__':
    main()