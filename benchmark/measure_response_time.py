"""
measure_response_time.py — End-to-end SPS latency measurement (Q1 methodology).

Latency = (actuator mitigation timestamp) − (earliest IDS detection timestamp)
for a single injected attack, computed from the container logs:

  * t_detect : the EARLIEST detection logged by any of the three IDS engines
               (Snort alert_fast.txt, Suricata fast.log, Zeek signatures.log);
  * t_act    : the first "RECEIVED action:" line appended by the actuator to
               /var/log/actuator_actions.log after that detection.

All timestamps are read from logs written inside Kathara containers, which
share the host kernel clock, so host-side comparisons are consistent. Every
parsed wall-clock timestamp is interpreted as UTC.

Exit codes: 0 = delta printed, 1 = no complete detection→mitigation pair found
(the polling caller may retry).
"""

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

# Tolerance for clock/scheduling skew between "detection" and "mitigation"
# log lines. Kept small: a value this large once allowed mitigations from the
# PREVIOUS attack to be paired with the current detection.
SKEW_EPS_S = 0.25


def run_cmd(cmd):
    """Esegue un comando shell in modo sincrono e restituisce l'output."""
    res = subprocess.run(cmd, shell=True, capture_output=True)
    return res.stdout.decode('utf-8', errors='replace')


def parse_with_formats(ts_str, formats):
    for fmt in formats:
        try:
            return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def parse_ids_time(cmd, regex, time_fmts, since, verbose=False, label=""):
    """Estrae i timestamp dai log testuali di Snort o Suricata."""
    out = run_cmd(cmd)
    timestamps = []
    skipped = 0
    for line in out.strip().split('\n'):
        if not line.strip(): continue
        m = re.search(regex, line)
        if m:
            ts_str = m.group(1)
            try:
                # Gestione dell'anno mancante nei log di Snort (formato MM/DD-…)
                if ts_str.count('/') == 1:
                    if any('%y' in fmt for fmt in time_fmts):
                        ts_str = f"{datetime.now().year % 100:02d}/{ts_str}"
                    elif any('%Y' in fmt for fmt in time_fmts):
                        ts_str = f"{datetime.now().year}/{ts_str}"

                ts = parse_with_formats(ts_str, time_fmts)
                if ts is None:
                    skipped += 1
                    if verbose:
                        print(f"[verbose:{label}] unparsed timestamp: {ts_str!r}",
                              file=sys.stderr)
                    continue
                if ts >= since:
                    timestamps.append(ts)
            except Exception:
                pass
    if verbose and skipped:
        print(f"[verbose:{label}] {skipped} line(s) with unparseable timestamps",
              file=sys.stderr)
    return timestamps


def get_zeek_time(lab_dir, since):
    """Estrae i timestamp UNIX dai log di Zeek."""
    out = run_cmd(f"kathara exec -d {lab_dir} ids_zeek -- tail -n 1000 /var/log/zeek/signatures.log")
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
    out = run_cmd(f"kathara exec -d {lab_dir} actuator -- tail -n 1000 /var/log/actuator_actions.log")
    timestamps = []
    for line in out.strip().split('\n'):
        if "RECEIVED action:" in line:
            m = re.search(r'^\[(.*?)\]', line)
            if m:
                # Support custom logs logging with milliseconds format=%(asctime)s.%(msecs)03d
                try:
                    ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc).timestamp()
                except ValueError:
                    ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()

                if ts >= since:
                    timestamps.append(ts)
    return timestamps


def main():
    parser = argparse.ArgumentParser(
        description="Measure IDS-detection → actuator-mitigation latency from lab logs."
    )
    parser.add_argument("lab_dir", nargs="?", default="../lab/quorum")
    parser.add_argument("--since", type=float, default=0.0,
                        help="Only consider log entries at/after this UNIX timestamp.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-source counts and parsing diagnostics.")
    args = parser.parse_args()

    # Snort 3 `-y` writes month-first stamps: MM/DD/YYYY-… or MM/DD/YY-… .
    # The year-first formats are tried as well so both conventions keep working;
    # strptime rejects impossible field values, so ambiguity is safe.
    snort_ts = parse_ids_time(
        f"kathara exec -d {args.lab_dir} ids_snort -- tail -n 1000 /var/log/snort/alert_fast.txt",
        r'^(\d{2,4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}(?:\.\d+)?)',
        [
            "%m/%d/%Y-%H:%M:%S.%f", "%m/%d/%y-%H:%M:%S.%f",
            "%m/%d/%Y-%H:%M:%S", "%m/%d/%y-%H:%M:%S",
            "%Y/%m/%d-%H:%M:%S.%f", "%y/%m/%d-%H:%M:%S.%f",
            "%Y/%m/%d-%H:%M:%S", "%y/%m/%d-%H:%M:%S",
        ],
        args.since, verbose=args.verbose, label="snort",
    )
    # Suricata fast.log wraps its stamp in square brackets: [MM/DD/YYYY-HH:MM:SS.mmm].
    suricata_ts = parse_ids_time(
        f"kathara exec -d {args.lab_dir} ids_suricata -- tail -n 1000 /var/log/suricata/fast.log",
        r'^\[?(\d{2}/\d{2}/\d{4}-\d{2}:\d{2}:\d{2}(?:\.\d+)?)',
        ["%m/%d/%Y-%H:%M:%S.%f", "%m/%d/%Y-%H:%M:%S"],
        args.since, verbose=args.verbose, label="suricata",
    )
    zeek_ts = get_zeek_time(args.lab_dir, args.since)

    if args.verbose:
        print(f"[verbose] detections since {args.since:.3f}: "
              f"snort={len(snort_ts)} suricata={len(suricata_ts)} zeek={len(zeek_ts)}",
              file=sys.stderr)

    all_ids_ts = snort_ts + suricata_ts + zeek_ts
    if not all_ids_ts:
        # Nessuna detection trovata, uscita silenziosa così il main file può riprovare
        sys.exit(1)

    # Prende la primissima detection registrata da uno qualsiasi degli IDS per questo attacco
    t_detect = min(all_ids_ts)

    actuator_ts = get_actuator_time(args.lab_dir, args.since)
    valid_mitigations = [t for t in actuator_ts if t >= (t_detect - SKEW_EPS_S)]

    if not valid_mitigations:
        sys.exit(1)

    # Prende la prima azione dell'actuator scatenata subito dopo la detection
    t_mitigate = min(valid_mitigations)

    delta = t_mitigate - t_detect
    print(f"Delta: {delta:.4f}s")

if __name__ == '__main__':
    main()
