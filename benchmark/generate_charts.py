import json
import matplotlib.pyplot as plt
import numpy as np
import os

print("Starting graph generation...")
os.makedirs('result', exist_ok=True)

print("Setting style...")
plt.style.use('seaborn-v0_8-whitegrid')

# ---------------------------------------------------------
# GRAPH 1: Capacity Results CometBFT
# ---------------------------------------------------------
try:
    print("Generating Graph 1...")
    with open('result/cometbft/capacity_results.json', 'r') as f:
        data_capacity = json.load(f)

    N_cap = [d["N"] for d in data_capacity]
    Sent_cap = [d["Sent"] for d in data_capacity]
    Detected_cap = [d["Detected"] for d in data_capacity]
    Ingress_cap = [d["Ingress"] for d in data_capacity]
    Trans_cap = [d["Transactions"] for d in data_capacity]

    Trans_cap = [max(1, t) for t in Trans_cap]

    fig, ax = plt.subplots(figsize=(10, 6))

    ax.plot(N_cap, Sent_cap, marker='o', linestyle='-', linewidth=5, markersize=8, color='#8C8C8C', alpha=0.4, label='Sent', zorder=2)
    ax.plot(N_cap, Detected_cap, marker='x', linestyle='--', linewidth=2, markersize=7, color='#4C72B0', label='Detected', zorder=3)
    
    ax.plot(N_cap, Ingress_cap, marker='^', linestyle='-', linewidth=2, markersize=8, color='#55A868', label='Ingress', zorder=4)
    ax.plot(N_cap, Trans_cap, marker='s', linestyle='-', linewidth=2, markersize=8, color='#C44E52', label='Transactions', zorder=5)

    ax.set_xscale('log')
    ax.set_yscale('log')
    ax.margins(x=0.03, y=0.03)

    ax.set_xlabel('Input Load', fontsize=22)
    ax.set_ylabel('Metric Count', fontsize=22)
    ax.tick_params(axis='both', which='major', labelsize=18)
    ax.legend(fontsize=20, loc='upper left', frameon=True, facecolor='white', framealpha=0.9, edgecolor='#CCCCCC')

    plt.tight_layout()
    plt.savefig('result/SPS_comparison.pdf', dpi=300)
    plt.close(fig)
except Exception as e:
    print(f"Error generating Graph 1: {e}")


# ---------------------------------------------------------
# GRAPHS 2 & 3: Comparison CometBFT vs Quorum
# ---------------------------------------------------------
try:
    print("Generating Graph 2/3...")
    with open('result/cometbft/blockchain_capacity.json', 'r') as f:
        cbft_data = json.load(f)

    with open('result/quorum/blockchain_capacity.json', 'r') as f:
        quorum_data = json.load(f)

    def extract_data(data):
        sent = [d["Sent"] for d in data]
        tps = [d["TPS"] for d in data]
        latency = [d.get("TotalTimeSeconds", d.get("WallTimeSeconds", 0)) for d in data] 
        trans = [d["Transactions"] for d in data]
        return sent, tps, latency, trans

    c_sent, c_tps, c_lat, c_trans = extract_data(cbft_data)
    q_sent, q_tps, q_lat, q_trans = extract_data(quorum_data)

    c_lat_plot = [max(0.01, l) for l in c_lat]
    q_lat_plot = [max(0.01, l) for l in q_lat]

    # ---------------------------------------------------------
    # Graph 2: Latency vs Sent
    # ---------------------------------------------------------
    fig, ax = plt.subplots(figsize=(10, 6))
    
    ax.plot(c_sent, c_lat_plot, marker='o', linestyle='-', linewidth=3.5, markersize=8, color='#4C72B0', alpha=0.85, label='CometBFT Latency', zorder=3)
    ax.plot(q_sent, q_lat_plot, marker='o', linestyle='-', linewidth=2.5, markersize=8, color='#C44E52', alpha=0.85, label='Quorum Latency', zorder=4)

    for i in range(len(c_sent)):
        is_c_above = c_lat_plot[i] >= q_lat_plot[i]
        
        c_offset = (0, 10) if is_c_above else (0, -12)
        c_va = 'bottom' if is_c_above else 'top'
        
        q_offset = (0, -12) if is_c_above else (0, 10)
        q_va = 'top' if is_c_above else 'bottom'

        # Annotazioni
        ax.annotate(f"{c_lat[i]:.2f}s", (c_sent[i], c_lat_plot[i]), 
                    textcoords="offset points", xytext=c_offset, ha='center', va=c_va, 
                    fontsize=12, fontweight='bold', color='#4C72B0')
            
        ax.annotate(f"{q_lat[i]:.2f}s", (q_sent[i], q_lat_plot[i]), 
                    textcoords="offset points", xytext=q_offset, ha='center', va=q_va, 
                    fontsize=12, fontweight='bold', color='#C44E52')

    ax.set_xscale('log')
    ax.set_yscale('log')
    ax.margins(x=0.03, y=0.08)

    ax.set_xlabel('Input Load (Transactions sent)', fontsize=22)
    ax.set_ylabel('Latency (Seconds)', fontsize=22)
    ax.tick_params(axis='both', which='major', labelsize=20)
    ax.legend(fontsize=20, loc='upper left', frameon=True, facecolor='white', framealpha=0.9, edgecolor='#CCCCCC')

    plt.tight_layout()
    plt.savefig('result/comparison_latency.pdf', dpi=300)
    plt.close(fig)

    # ---------------------------------------------------------
    # Graph 3: Transactions vs Sent
    # ---------------------------------------------------------
    c_trans_plot = [max(1, t) for t in c_trans]
    q_trans_plot = [max(1, t) for t in q_trans]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(c_sent, c_sent, marker='', linestyle='--', linewidth=2, color='#8C8C8C', label='Ideal (Trans = Sent)', zorder=1)
    
    ax.plot(c_sent, c_trans_plot, marker='o', linestyle='-', linewidth=4.5, markersize=8, color='#4C72B0', alpha=0.85, label='CometBFT Trans', zorder=3)
    ax.plot(q_sent, q_trans_plot, marker='o', linestyle='-', linewidth=2, markersize=6, color='#C44E52', alpha=0.85, label='Quorum Trans', zorder=4)

    for i in range(len(c_sent)):
        is_c_above = c_trans_plot[i] >= q_trans_plot[i]
        c_offset = (0, 10) if is_c_above else (0, -12)
        c_va = 'bottom' if is_c_above else 'top'
        
        # Annotazioni
        ax.annotate(f"{c_tps[i]:.0f} TPS", (c_sent[i], c_trans_plot[i]), 
                    textcoords="offset points", xytext=c_offset, ha='center', va=c_va, 
                    fontsize=12, fontweight='bold', color='#4C72B0')
        
    for i in range(len(q_sent)):
        is_c_above = c_trans_plot[i] >= q_trans_plot[i]
        q_offset = (0, -12) if is_c_above else (0, 10)
        q_va = 'top' if is_c_above else 'bottom'
        if i != len(q_sent) - 2:
        # Annotazioni
            ax.annotate(f"{q_tps[i]:.0f} TPS", (q_sent[i], q_trans_plot[i]), 
                        textcoords="offset points", xytext=q_offset, ha='center', va=q_va, 
                        fontsize=12, fontweight='bold', color='#C44E52')

    ax.set_xscale('log')
    ax.set_yscale('log')
    ax.margins(x=0.04, y=0.08)

    ax.set_xlabel('Input Load (Transactions sent)', fontsize=22)
    ax.set_ylabel('Processed Transactions', fontsize=22)
    ax.tick_params(axis='both', which='major', labelsize=20)
    ax.legend(fontsize=20, loc='upper left', frameon=True, facecolor='white', framealpha=0.9, edgecolor='#CCCCCC')

    plt.tight_layout()
    plt.savefig('result/comparison_transactions.pdf', dpi=300)
    plt.close(fig)
except Exception as e:
    print(f"Error generating Graph 2/3: {e}")

# ---------------------------------------------------------
# GRAPH 4: Boxplot Response Time
# ---------------------------------------------------------
try:
    print("Generating Graph 4...")
    import glob

    def find_latest_data(lab_type):
        files = glob.glob(f'result/{lab_type}/data_N*.json')
        if not files:
            return None
        # Sort by N (extract number from filename)
        files.sort(key=lambda x: int(re.search(r'N(\d+)', x).group(1)) if re.search(r'N(\d+)', x) else 0, reverse=True)
        return files[0]

    import re
    q_file = find_latest_data('quorum')
    c_file = find_latest_data('cometbft')

    if not q_file or not c_file:
        print(f"Skipping Graph 4: Missing data files (Quorum: {q_file}, CometBFT: {c_file})")
    else:
        with open(q_file, 'r') as f:
            quorum_data = json.load(f)
        with open(c_file, 'r') as f:
            comet_data = json.load(f)

        q_mean = sum(quorum_data) / len(quorum_data)
        c_mean = sum(comet_data) / len(comet_data)

        # Extract N for label
        q_n = re.search(r'N(\d+)', q_file).group(1)
        c_n = re.search(r'N(\d+)', c_file).group(1)

        q_label = f"Quorum (N={q_n})\nMean: {q_mean:.4f}s"
        c_label = f"CometBFT (N={c_n})\nMean: {c_mean:.4f}s"

    fig, ax = plt.subplots(figsize=(10, 6))

    bplot = ax.boxplot([quorum_data, comet_data],
                       patch_artist=True,
                       labels=[q_label, c_label],
                       widths=0.15,
                       medianprops=dict(color="#333333", linewidth=2),
                       boxprops=dict(linewidth=1, color="#333333", alpha=0.9),
                       whiskerprops=dict(linewidth=1, color="#333333"),
                       capprops=dict(linewidth=1, color="#333333"))

    colors = ['#8CA8D1', '#8DCB9E'] 
    for patch, color in zip(bplot['boxes'], colors):
        patch.set_facecolor(color)

    ax.set_ylabel('Response Time (seconds)', fontsize=24)
    ax.set_xlabel('Technology', fontsize=24)
    ax.tick_params(axis='both', which='major', labelsize=20)

    plt.tight_layout()
    plt.savefig('result/Response_Time.pdf', dpi=300)
    plt.close(fig)
except Exception as e:
    print(f"Error generating Graph 4: {e}")