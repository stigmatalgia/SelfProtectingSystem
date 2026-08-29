# SelfProtectingSystem — Makefile
# Usage:
#   make cometbft <target>   — apply target to lab/cometbft
#   make quorum   <target>   — apply target to lab/quorum (default)
#
# Benchmarks map to the paper (DLT26) as follows:
#   measure / chart            → Q1: end-to-end IDS→actuator response latency
#   blockchain-benchmark       → Q2: sustained ledger throughput under bursts
#   capacity                   → Q3: deduplication effect on on-chain tx load
#                                (CometBFT-only, as in the paper)
#   all-benchmarks             → full suite for both labs + final charts

ROOT := $(CURDIR)

LAB_TYPE := quorum
BASE_DIR := lab/quorum
ifneq ($(filter cometbft,$(MAKECMDGOALS)),)
    LAB_TYPE := cometbft
    BASE_DIR := lab/cometbft
endif

ENV_VARS := PYTHONIOENCODING=utf-8 LC_ALL=C.UTF-8 LANG=C.UTF-8
PYTHON   := $(ENV_VARS) $(ROOT)/.venv/bin/python

N ?= 10

.DEFAULT_GOAL := help
.PHONY: quorum cometbft bench-build build generate-config start clean clean-config \
        measure chart capacity blockchain-benchmark all-benchmarks charts setup help

quorum:
	@if [ "$(MAKECMDGOALS)" = "quorum" ]; then echo "Use: make quorum <target>"; fi

cometbft:
	@if [ "$(MAKECMDGOALS)" = "cometbft" ]; then echo "Use: make cometbft <target>"; fi

# ── Docker images ────────────────────────────────────────────────────────────
build:
	@echo "Building Juice Shop..."
	docker build -t juice_shop $(BASE_DIR)/juice_shop
	@echo "Building Attacker..."
	docker build -t attacker_carbonyl $(BASE_DIR)/attacker
	@echo "Building Snort IDS..."
	docker build -t ids_snort $(BASE_DIR)/ids_snort
	@echo "Building Suricata IDS..."
	docker build -t ids_suricata $(BASE_DIR)/ids_suricata
	@echo "Building Zeek IDS..."
	docker build -t ids_zeek $(BASE_DIR)/ids_zeek
ifeq ($(LAB_TYPE),cometbft)
	@echo "Building cometbft Docker image (sps-node via multi-stage build)..."
	docker build -t kathara/cometbft -f $(BASE_DIR)/shared/Dockerfile $(BASE_DIR)/shared
else
	@echo "Building Quorum blockchain image..."
	docker build -t kathara/quorum -f $(BASE_DIR)/shared/Dockerfile $(BASE_DIR)
endif
	@echo "Building Actuator..."
	docker build -t actuator $(BASE_DIR)/actuator

# ── Config generation ────────────────────────────────────────────────────────
generate-config:
	@echo "Generating blockchain configuration for $(LAB_TYPE)..."
ifeq ($(LAB_TYPE),cometbft)
	$(PYTHON) generate_cometbft_config.py
else
	$(PYTHON) generate_blockchain_config.py
endif

# ── Lab lifecycle ────────────────────────────────────────────────────────────
start:
	@echo "Starting Kathara lab in $(BASE_DIR)..."
	$(ENV_VARS) kathara lstart -d $(BASE_DIR)

clean:
	@echo "Stopping Kathara lab in $(BASE_DIR)..."
	$(ENV_VARS) kathara lclean -d $(BASE_DIR)

clean-config:
	@echo "Cleaning generated configs in $(BASE_DIR)..."
	kathara lclean -d $(BASE_DIR) 2>/dev/null || true
ifeq ($(LAB_TYPE),cometbft)
	cd $(BASE_DIR) && rm -rf shared/validator* shared/light* shared/fullnode*
	cd $(BASE_DIR) && rm -rf shared/handshake
	cd $(BASE_DIR) && rm -f shared/chain_ready shared/sps-node
	cd $(BASE_DIR) && rm -rf shared/sps-chain/target shared/sps-bench/target shared/__pycache__ && rm -rf target/
	rm -rf benchmark/native/target
else
	cd $(BASE_DIR) && rm -rf validator0/data validator1/data validator2/data
	cd $(BASE_DIR) && rm -rf member0/data member1/data member2/data member3/data
	rm -rf $(BASE_DIR)/generated_configurations
endif
	cd $(BASE_DIR) && rm -rf shared/ssh
	rm -f $(BASE_DIR)/shared/contract_address.txt $(BASE_DIR)/shared/contract_abi.json
	rm -f $(BASE_DIR)/shared/disable_negative_alerts
	rm -rf benchmark/__pycache__

# ── Benchmarks ───────────────────────────────────────────────────────────────
measure:
	@echo "Measuring single-shot response time for $(LAB_TYPE)..."
	cd benchmark && $(PYTHON) measure_response_time.py ../$(BASE_DIR)

chart:
	@echo "Running N sequential attacks + boxplot for $(LAB_TYPE) (N=$(N))..."
	cd benchmark && $(PYTHON) blockchain_measure.py ../$(BASE_DIR) $(N)

capacity:
ifeq ($(LAB_TYPE),cometbft)
	@echo "Running capacity benchmark for CometBFT (paper Q3, deduplication active)..."
	cd benchmark && $(PYTHON) benchmark_capacity.py ../$(BASE_DIR)
else
	@echo "SKIPPED: the capacity/deduplication benchmark is CometBFT-only by design"
	@echo "(paper Q3): with GoQuorum the transaction volume is already too low"
	@echo "for any difference to emerge. Use 'make cometbft capacity'."
endif

blockchain-benchmark:
	@echo "Running native ledger throughput benchmark for $(LAB_TYPE) (paper Q2)..."
	cd benchmark && $(PYTHON) blockchain_benchmark.py ../$(BASE_DIR) \
	    --concurrency 256 --sleep-ms 0 --mode async

charts:
	@echo "Generating comparison charts from result/*.json..."
	cd benchmark && MPLCONFIGDIR=/tmp/.mplconfig ../.venv/bin/python generate_charts.py

# ── Pre-compile sps-bench (native P2P injector) ───────────────────────────
# Produces a musl static binary that blockchain_benchmark.py copies into
# the running Kathara containers — no Rust toolchain needed inside them.
bench-build:
	@echo "Building sps-bench (musl static binary)..."
	cargo build --release \
		--target x86_64-unknown-linux-musl \
		--manifest-path lab/cometbft/shared/sps-bench/Cargo.toml
	@echo "Binary → lab/cometbft/shared/sps-bench/target/x86_64-unknown-linux-musl/release/sps-bench"
	@cp lab/cometbft/shared/sps-bench/target/x86_64-unknown-linux-musl/release/sps-bench \
	   lab/cometbft/shared/sps-bench/sps-bench
	@echo "Copied → lab/cometbft/shared/sps-bench/sps-bench (ready for deployment)"

# Alias kept for backwards compatibility of docs.
rust-build: bench-build

# ── Full setup shortcut ───────────────────────────────────────────────────────
setup: build generate-config start

# ── All Benchmarks ───────────────────────────────────────────────────────────
all-benchmarks:
	@echo "=== Starting Full Benchmark Suite for Quorum ==="
	$(MAKE) quorum clean-config
	$(MAKE) quorum setup
	$(MAKE) quorum chart N=100
	$(MAKE) quorum clean-config
	$(MAKE) quorum setup
	$(MAKE) quorum blockchain-benchmark
	$(MAKE) quorum clean-config
	@echo "=== Starting Full Benchmark Suite for CometBFT ==="
	$(MAKE) cometbft clean-config
	$(MAKE) cometbft setup
	$(MAKE) cometbft chart N=100
	$(MAKE) cometbft clean-config
	$(MAKE) cometbft setup
	$(MAKE) cometbft blockchain-benchmark
	$(MAKE) cometbft clean-config
	$(MAKE) cometbft setup
	$(MAKE) cometbft capacity
	$(MAKE) cometbft clean-config
	@echo "=== Generating Final Charts ==="
	$(MAKE) charts
	@echo "All benchmarks completed and charts generated in benchmark/result!"

# ── Help ───────────────────────────────────────────────────────────────────
help:
	@echo "SelfProtectingSystem Makefile"
	@echo ""
	@echo "Environment prefixes:"
	@echo "  make cometbft <target>  — native Rust SPS-Chain (lab/cometbft)"
	@echo "  make quorum   <target>  — Quorum EVM   (lab/quorum, default)"
	@echo ""
	@echo "Targets:"
	@echo "  build                Build all Docker images"
	@echo "  generate-config      Generate node config files"
	@echo "  start                Start Kathara lab"
	@echo "  clean                Stop Kathara lab"
	@echo "  clean-config         Remove all generated configs + stop lab"
	@echo "  setup                Full setup: build + generate-config + start"
	@echo ""
	@echo "Benchmarks (on a running lab):"
	@echo "  measure              One-shot IDS→Actuator response-time delta"
	@echo "  chart N=100          N sequential attacks + boxplot (Q1; default N=10)"
	@echo "  blockchain-benchmark Native ledger throughput bursts (Q2)"
	@echo "  capacity             Dedup/on-chain-load test (Q3, CometBFT only)"
	@echo "  charts               Regenerate comparison charts from saved JSON"
	@echo "  all-benchmarks       Full suite for both labs + final charts"
	@echo "  bench-build          Pre-compile sps-bench (musl static binary)"
