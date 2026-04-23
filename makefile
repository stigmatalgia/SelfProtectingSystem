# SelfProtectingSystem — Makefile
# Usage:
#   make cometbft <target>   — apply target to lab/cometbft
#   make quorum   <target>   — apply target to lab/quorum (default)

ifneq ($(filter cometbft,$(MAKECMDGOALS)),)
    LAB_TYPE := cometbft
    BASE_DIR := lab/cometbft
    ENV_VARS := PYTHONIOENCODING=utf-8 LC_ALL=C.UTF-8 LANG=C.UTF-8
else
    LAB_TYPE := quorum
    BASE_DIR := lab/quorum
    ENV_VARS := PYTHONIOENCODING=utf-8 LC_ALL=C.UTF-8 LANG=C.UTF-8
endif

.PHONY: quorum cometbft rust-build build generate-config start clean clean-config \
        measure chart capacity blockchain-benchmark bench-build setup help

quorum:
	@if [ "$(MAKECMDGOALS)" = "quorum" ]; then echo "Use: make quorum <target>"; fi

cometbft:
	@if [ "$(MAKECMDGOALS)" = "cometbft" ]; then echo "Use: make cometbft <target>"; fi

# Compilation of sps-node is handled inside the Dockerfile via multi-stage build.

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
	$(ENV_VARS) .venv/bin/python generate_cometbft_config.py
else
	$(ENV_VARS) .venv/bin/python generate_blockchain_config.py
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
	cd $(BASE_DIR) && rm -rf validator*/data light*/data fullnode*/data
	cd $(BASE_DIR) && rm -f shared/chain_ready shared/sps-node
	cd $(BASE_DIR) && rm -rf shared/sps-chain/target shared/sps-bench/target && rm -rf target/
	rm -rf benchmark/native/target
else
	cd $(BASE_DIR) && rm -rf validator0/data validator1/data validator2/data
	cd $(BASE_DIR) && rm -rf member0/data member1/data member2/data member3/data
	cd $(BASE_DIR) && rm -rf ../resources/blockchain_configurations
endif
	cd $(BASE_DIR) && rm -rf shared/ssh
	rm -f $(BASE_DIR)/shared/contract_address.txt $(BASE_DIR)/shared/contract_abi.json
	rm -f $(BASE_DIR)/shared/disable_negative_alerts
	rm -rf benchmark/__pycache__

# ── Benchmarks ───────────────────────────────────────────────────────────────
N ?= 10
measure:
	@echo "Measuring response time for $(LAB_TYPE)..."
	cd benchmark && $(ENV_VARS) ../.venv/bin/python measure_response_time.py ../$(BASE_DIR)

chart:
	@echo "Generating chart for $(LAB_TYPE) (N=$(N) attacks)..."
	cd benchmark && $(ENV_VARS) ../.venv/bin/python generate_chart.py ../$(BASE_DIR) $(N)

capacity:
	@echo "Running capacity benchmark for $(LAB_TYPE)..."
	cd benchmark && $(ENV_VARS) ../.venv/bin/python benchmark_capacity.py ../$(BASE_DIR)

blockchain-benchmark:
	@echo "Running native P2P blockchain benchmark for $(LAB_TYPE)..."
	cd benchmark && $(ENV_VARS) ../.venv/bin/python blockchain_benchmark.py ../$(BASE_DIR)

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
	$(MAKE) quorum capacity
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
	$(MAKE) cometbft capacity
	$(MAKE) cometbft clean-config
	$(MAKE) cometbft setup
	$(MAKE) cometbft blockchain-benchmark
	$(MAKE) cometbft clean-config
	@echo "=== Generating Final Charts ==="
	cd benchmark && $(ENV_VARS) ../.venv/bin/python generate_graphs.py
	@echo "All benchmarks completed and charts generated in benchmark/result!"

# ── Help ───────────────────────────────────────────────────────────────────
help:
	@echo "SelfProtectingSystem Makefile"
	@echo ""
	@echo "Environment prefixes:"
	@echo "  make cometbft <target>  — native Rust SPS-Chain (lab/cometbft)"
	@echo "  make quorum   <target>  — Quorum EVM (lab/quorum, unchanged)"
	@echo ""
	@echo "Targets:"
	@echo "  rust-build          Build the sps-node Rust binary (musl static)"
	@echo "  build               Build all Docker images"
	@echo "  generate-config     Generate node config files"
	@echo "  start               Start Kathara lab"
	@echo "  clean               Stop Kathara lab"
	@echo "  clean-config        Remove all generated configs + stop lab"
	@echo "  measure             Measure IDS→Actuator response time"
	@echo "  chart               Generate boxplot chart (N attacks, e.g. make chart N=10)"
	@echo "  capacity            Full IDS+blockchain capacity benchmark"
	@echo "  bench-build          Pre-compile sps-bench (musl) for native P2P injection"
	@echo "  blockchain-benchmark Native P2P blockchain throughput benchmark"
	@echo "  all-benchmarks      Run all benchmarks (quorum & cometbft), restart lab between each, and generate charts"
	@echo "  setup               Full setup: build + generate-config + start"
	@echo "  help                Show this help"

%:
	@: