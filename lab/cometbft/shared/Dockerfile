# ── Stage 1: Build sps-node (static Rust binary) ────────────────────────────
FROM rust:slim-bookworm AS builder

RUN apt-get update && apt-get install -y musl-tools pkg-config && rm -rf /var/lib/apt/lists/*
RUN rustup target add x86_64-unknown-linux-musl

WORKDIR /build
COPY sps-chain/Cargo.toml sps-chain/Cargo.toml

# Cache dependency compilation — only rebuild if Cargo.toml changes
RUN mkdir -p sps-chain/src && echo 'fn main(){}' > sps-chain/src/main.rs
RUN cd sps-chain && cargo build --release --target x86_64-unknown-linux-musl 2>/dev/null || true

# Build sps-chain
COPY sps-chain/src sps-chain/src
RUN touch sps-chain/src/main.rs && \
    cd sps-chain && \
    cargo build --release --target x86_64-unknown-linux-musl

# Build sps-bench
COPY sps-bench sps-bench
RUN cd sps-bench && \
    cargo build --release --target x86_64-unknown-linux-musl

# ── Stage 2: Runtime image (kathara/base + sps-node) ────────────────────────
FROM kathara/base

COPY --from=builder \
    /build/sps-chain/target/x86_64-unknown-linux-musl/release/sps-node \
    /usr/local/bin/sps-node
COPY --from=builder \
    /build/sps-bench/target/x86_64-unknown-linux-musl/release/sps-bench \
    /usr/local/bin/sps-bench
RUN chmod +x /usr/local/bin/sps-node /usr/local/bin/sps-bench

# Runtime deps: Python3 (alert_forwarder, actuator_server), curl, debug tools
RUN apt-get update && \
    apt-get install -y python3 python3-pip curl nano tcpdump jq && \
    rm -rf /var/lib/apt/lists/*

# Verify binary works
RUN sps-node --help 2>&1 || true

WORKDIR /home/cometbft

ENTRYPOINT ["/bin/bash"]
