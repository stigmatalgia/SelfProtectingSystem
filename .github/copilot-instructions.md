# Copilot Instructions for SelfProtectingSystem

## Overview
**SPS-Blockchain** is a cyber-resilient system that combines intrusion detection systems (IDS), blockchain consensus, and automated mitigation. It supports two distinct blockchain implementations: **Quorum (QBFT)** and **CometBFT**.

## Build & Setup

### Prerequisites
- Python 3 with `venv`
- Docker
- Kathara (container orchestration for labs)
- Cargo (for CometBFT builds only)

### Environment Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Build Commands
All building and lab management is done through the **Makefile**. The makefile supports two blockchain environments:

```bash
# For Quorum (default): make quorum <target>
# For CometBFT:        make cometbft <target>

make quorum setup              # Full setup: build images + generate config + start lab
make quorum build              # Build all Docker images only
make quorum generate-config    # Generate blockchain node configs
make quorum start              # Start Kathara lab
make quorum clean              # Stop Kathara lab
make quorum clean-config       # Stop lab + delete all generated configs

# Same for CometBFT
make cometbft setup
make cometbft build
# ... etc
```

### CometBFT Rust Binary (Native Benchmarking)
The CometBFT environment includes a native Rust benchmarking tool:
```bash
make bench-build  # Pre-compiles sps-bench as musl static binary
                  # Output: lab/cometbft/shared/sps-bench/sps-bench
```

## Lab Architecture

The system simulates an attack detection and response loop:

### Dual Blockchain Topologies

**Quorum (QBFT):**
- 3 **Validator** nodes (consensus engines)
- 4 **Member** nodes (API gateways + monitoring)
  - Members 0–2: HTTP alert ingestion from IDS systems
  - Member 3: Blockchain monitoring + actuation forwarding
- IDS systems: Snort, Suricata, Zeek (promiscuous traffic mirroring)
- Juice Shop (target web application)
- Actuator (executes mitigation via SSH)

**CometBFT:**
- 3 **Validator** nodes (consensus engines)
- 3 **Light Nodes** (HTTP alert ingestion from IDS)
- 1 **Full Node** (blockchain monitoring + actuation)
- Same IDS and Actuator setup as Quorum

### Lab Directories
- `lab/quorum/` — Quorum QBFT network definition
- `lab/cometbft/` — CometBFT network definition (includes Rust sps-chain)
- Both follow Kathara structure: `<node_name>/` directories with startup scripts

## Benchmarking Suite

### Commands
```bash
# Single response-time measurement (on running lab)
make quorum measure

# Generate boxplot from N sequential attacks
make quorum chart N=10   # Default N=10

# Full capacity benchmark
make quorum capacity

# Native P2P blockchain throughput benchmark
make quorum blockchain-benchmark

# Run all benchmarks (both quorum + cometbft, auto restart between each)
make all-benchmarks
```

### Output
Results saved to `benchmark/result/<lab_type>/` as JSON data and PNG charts.

### Measurement Scripts
- **`measure_response_time.py`** — Parses IDS and Actuator logs to compute IDS-detection → Actuator-action delta
- **`blockchain_measure.py`** — Runs sequential attack simulations, records response times, generates boxplot
- **`benchmark_capacity.py`** — Tests IDS + blockchain capacity under load
- **`blockchain_benchmark.py`** — Native P2P blockchain throughput (CometBFT)

## Key Conventions

### Configuration Generation
- **`generate_blockchain_config.py`** — Generates Quorum node configs (addresses, genesis, bootnodes)
- **`generate_cometbft_config.py`** — Generates CometBFT node configs
- Both create `shared/` subdirectory files consumed by Kathara startup scripts

### Blockchain Integration

**Quorum (JavaScript):**
- `lab/quorum/shared/contract/blockchain_api.js` — HTTP server on Member nodes (port 3000)
  - Receives alerts from IDS systems via HTTP POST
  - Submits votes to smart contract via Web3 + Geth IPC
  - Manages transaction queue, nonce, retries
  - Consensus threshold: 2/3 validators

**CometBFT (Rust):**
- `lab/cometbft/shared/sps-chain/` — Full application state machine
  - `main.rs` — ABCI server (Tendermint RPC)
  - `api.rs` — HTTP listener (alert ingestion)
  - `forwarder.rs` — Blockchain event monitoring + actuation
  - `ledger.rs` — State voting logic + action threshold

### Alert Types
Standard alert constants (used across all IDS forwarders and blockchain nodes):
```python
'SAFE_ENVIRONMENT', 'SQL_INJECTION', 'XSS_ATTACK', 'PATH_TRAVERSAL', 'COMMAND_INJECTION'
```

### Kathara Container Execution
Within scripts, Kathara containers are accessed via:
```bash
kathara exec <node_name> -- <command>
```
Environment variables are set in makefile (UTF-8 encoding for Kathara compatibility).

## File Organization

### Core Python Scripts
- Root level: Config generators, makefile
- `benchmark/` — All benchmarking and measurement scripts
- `lab/{quorum|cometbft}/shared/` — Python IDS helpers, alert forwarders

### Lab Node Structure (per blockchain type)
```
lab/quorum/
├── lab.conf                    # Kathara network topology
├── validator{0,1,2}/
├── member{0,1,2,3}/
├── ids_snort/ ids_suricata/ ids_zeek/
├── actuator/
├── juice_shop/
├── router.startup
└── shared/                     # Generated configs, contracts, SSH keys
    ├── contract/               # Quorum smart contracts + deployment
    └── sps-chain/              # CometBFT app (Rust)
```

## Common Development Tasks

### Testing Individual Attacks
```bash
kathara exec attacker -- curl 'http://10.0.0.80:3000/rest/products/search?q=<SQL_INJECTION>'
```
See README.md for additional test payloads.

### Accessing Blockchain State (Quorum)
```bash
kathara exec member3 -- sh -c "geth attach /home/qbft/data/geth.ipc --exec \"...JSON-RPC commands...\""
```

### Modifying Node Behavior
1. Edit startup scripts in `lab/{blockchain}/shared/` (e.g., `ids_feedback_server.py`)
2. Rebuild images: `make {quorum|cometbft} build`
3. Re-run lab: `make {quorum|cometbft} setup`

### Debugging Python Scripts
- Environment encoding is critical: scripts set UTF-8 explicitly to avoid Kathara encoding issues
- Benchmark scripts run shell commands with subprocess; check stdout/stderr for failures

## Important Notes

- **Startup time:** Lab setup can take 30–60 seconds due to Docker image pulls and genesis configuration
- **Blockchain selection:** Makefile defaults to **Quorum**; use `make cometbft <target>` explicitly for CometBFT
- **Clean between runs:** Always run `make <blockchain> clean-config` before switching blockchain types or running new benchmarks
- **Virtual environment required:** Python dependencies (old setuptools/wheel) may conflict; venv isolation is mandatory
- **Consensus threshold:** Decisions require 2/3 validator agreement; system tolerates 1 Byzantine validator
