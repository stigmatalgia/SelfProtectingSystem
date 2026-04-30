#!/usr/bin/env python3
"""
generate_cometbft_config.py — Native SPS-Chain config generator.

Replaces the old evmosd/bootlab approach entirely.
Generates sps_config.toml, CometBFT config.toml, genesis.json, and all 
required Ed25519 cryptographic keys for every node directly in Python.
"""

import os
import json
import base64
import hashlib
import subprocess
import logging

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
except ImportError:
    exit(1)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("sps-config-gen")

# ── Network topology ────────────────────────────────────────────────────────
VALIDATORS = {
    "validator0": "10.99.0.1",
    "validator1": "10.99.0.2",
    "validator2": "10.99.0.3",
}

AGENTS = {
    "light0": {"mgmt": "172.16.1.10", "blockchain": "10.99.0.11"},
    "light1": {"mgmt": "172.16.2.10", "blockchain": "10.99.0.12"},
    "light2": {"mgmt": "172.16.3.10", "blockchain": "10.99.0.13"},
}

FULLNODE = {
    "fullnode0": {"actuator_net": "172.16.4.10", "blockchain": "10.99.0.14"},
}

P2P_PORT  = 26656
API_PORT  = 3000
BASE_DIR  = os.path.join("lab", "cometbft")
SHARED_DIR = os.path.join(BASE_DIR, "shared")

# Attack parameters tracked by the IDS ledger
PARAMETERS = ["SQL_INJECTION", "XSS_ATTACK", "PATH_TRAVERSAL", "COMMAND_INJECTION"]
AGENTS_COUNT = 3   # majority threshold (votes > agents_count/2)

# Actuator endpoint
ACTUATOR_URL = "http://172.16.4.1:5000/action"


# ── Key generation ──────────────────────────────────────────────────────────

def generate_ed25519_identity():
    """Generates a valid Tendermint Ed25519 keypair and Node ID."""
    priv = ed25519.Ed25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    
    # CometBFT stores priv_key as 64 bytes (32 priv + 32 pub)
    priv_b64 = base64.b64encode(priv_bytes + pub_bytes).decode('utf-8')
    pub_b64 = base64.b64encode(pub_bytes).decode('utf-8')
    
    # ID/Address is the first 20 bytes of SHA256(pub_bytes)
    node_id = hashlib.sha256(pub_bytes).digest()[:20].hex()
    
    return node_id.lower(), node_id.upper(), pub_b64, priv_b64


# ── Config TOML builders ────────────────────────────────────────────────────

def build_sps_config(node_id: str, role: str, listen_ip: str, peers: list[str], actuator_url: str = "", disable_dedup: bool = False) -> str:
    """Render the application specific sps_config.toml"""
    peer_list = ", ".join(f'"{p}"' for p in peers)
    actuator_section = f'\n[actuator]\nurl = "{actuator_url}"\n' if actuator_url else ""
    dedup_val = "true" if disable_dedup else "false"

    return f"""# sps-node configuration

[node]
id       = "{node_id}"
role     = "{role}"
listen_addr = "0.0.0.0:{P2P_PORT}"
api_addr    = "0.0.0.0:{API_PORT}"

[consensus]
timeout_propose_ms = 200
timeout_commit_ms  = 200
block_max_txs      = 500000

[ledger]
parameters   = {json_list(PARAMETERS)}
agents_count = {AGENTS_COUNT}
disable_dedup = {dedup_val}

[peers]
persistent = [{peer_list}]
{actuator_section}
"""

def build_comet_config(peers: list[str]) -> str:
    """Render the native CometBFT config.toml tuned for raw TPS benchmarks."""
    peers_str = ",".join(peers)
    
    return f"""proxy_app = "tcp://127.0.0.1:26658"

[rpc]
laddr = "tcp://0.0.0.0:26657"
max_open_connections = 4096
max_body_bytes = 10000000

[p2p]
laddr = "tcp://0.0.0.0:26656"
persistent_peers = "{peers_str}"
addr_book_strict = false
allow_duplicate_ip = true
flush_throttle_timeout = "10ms"
send_rate = 20971520
recv_rate = 20971520
max_num_inbound_peers = 80
max_num_outbound_peers = 40

[mempool]
size = 50000
cache_size = 20000
max_txs_bytes = 2684354560
max_tx_bytes = 262144
recheck = false
[consensus]
# Limiti di "pazienza" alti: se la rete è lenta sotto sforzo, i nodi aspettano senza fallire.
# Se la rete è veloce, voteranno istantaneamente in pochi millisecondi.
timeout_propose = "2s"
timeout_propose_delta = "200ms"
timeout_prevote = "2s"
timeout_prevote_delta = "200ms"
timeout_precommit = "2s"
timeout_precommit_delta = "200ms"

# ZERO PAUSE: Non appena il blocco è approvato, passa subito al successivo.
timeout_commit = "50ms"

skip_timeout_commit = true
peer_gossip_sleep_duration = "50ms"

create_empty_blocks = false
create_empty_blocks_interval = "500ms"
"""

def json_list(items: list[str]) -> str:
    return f"[" + ", ".join(f'"{i}"' for i in items) + "]"

# ── Write helpers ───────────────────────────────────────────────────────────

def write_json(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def write_file(path: str, content: str):
    with open(path, "w") as f:
        f.write(content)

# ── SSH key generation ──────────────────────────────────────────────────────

def generate_ssh_keys():
    ssh_dir = os.path.join(SHARED_DIR, "ssh")
    key_path = os.path.join(ssh_dir, "actuator_id_ed25519")
    if os.path.exists(key_path):
        log.info("SSH keys already exist — skipping.")
        return
    os.makedirs(ssh_dir, exist_ok=True)
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", "", "-C", "actuator@sps"], check=True)
    log.info("SSH keys generated in %s", ssh_dir)

# ── Main ────────────────────────────────────────────────────────────────────

def generate_all():
    log.info("=== SPS-Chain Config Generator ===")
    
    os.makedirs(SHARED_DIR, exist_ok=True)
    
    all_nodes = list(VALIDATORS.keys()) + list(AGENTS.keys()) + list(FULLNODE.keys())
    identities = {}
    genesis_validators = []

    # 1. Generate IDs and Crypto for all nodes
    for name in all_nodes:
        n_id, address, pub_b64, priv_b64 = generate_ed25519_identity()
        identities[name] = {"id": n_id, "address": address, "pub": pub_b64, "priv": priv_b64}
        
        # Save validator specific info for genesis
        if name in VALIDATORS:
            genesis_validators.append({
                "address": address,
                "pub_key": {"type": "tendermint/PubKeyEd25519", "value": pub_b64},
                "power": "10",
                "name": name
            })
        log.info("  Node %-12s → id=%s", name, n_id)

# 2. Build Genesis Template
    genesis = {
        "genesis_time": "2026-01-01T00:00:00Z",
        "chain_id": "sps-chain-1",
        "initial_height": "1",
        "consensus_params": {
            "block": {"max_bytes": "524288", "max_gas": "-1", "time_iota_ms": "1000"},
            "evidence": {"max_age_num_blocks": "100000", "max_age_duration": "172800000000000", "max_bytes": "524288"},
            "validator": {"pub_key_types": ["ed25519"]},
            "version": {}
        },
        "validators": genesis_validators,
        "app_hash": ""
    }

    # 3. Setup Networking and Peers
    validator_peers = [f"{identities[n]['id']}@{ip}:{P2P_PORT}" for n, ip in VALIDATORS.items()]
    all_blockchain_peers = validator_peers + [
        f"{identities['fullnode0']['id']}@{FULLNODE['fullnode0']['blockchain']}:{P2P_PORT}"
    ]

    # 4. Generate configs for every node
    for name in all_nodes:
        node_dir = os.path.join(SHARED_DIR, name)
        os.makedirs(node_dir, exist_ok=True)
        
        # Role & Networking
        if name in VALIDATORS:
            role = "validator"
            listen_ip = VALIDATORS[name]
            my_peers = [p for p in validator_peers if identities[name]["id"] not in p]
            actuator = ""
        elif name in AGENTS:
            role = "agent"
            listen_ip = AGENTS[name]["blockchain"]
            my_peers = all_blockchain_peers
            actuator = ""
        else:
            role = "fullnode"
            listen_ip = FULLNODE[name]["blockchain"]
            my_peers = all_blockchain_peers
            actuator = ACTUATOR_URL

        # Write SPS Config
        sps_cfg = build_sps_config(identities[name]["id"], role, listen_ip, my_peers, actuator)
        write_file(os.path.join(node_dir, "sps_config.toml"), sps_cfg)
        
        # Write Comet Config
        comet_cfg = build_comet_config(my_peers)
        write_file(os.path.join(node_dir, "config.toml"), comet_cfg)
        
        # Write Genesis
        write_json(os.path.join(node_dir, "genesis.json"), genesis)
        
        # Write Cryptographic Files
        node_key = {"priv_key": {"type": "tendermint/PrivKeyEd25519", "value": identities[name]["priv"]}}
        priv_validator = {
            "address": identities[name]["address"],
            "pub_key": {"type": "tendermint/PubKeyEd25519", "value": identities[name]["pub"]},
            "priv_key": {"type": "tendermint/PrivKeyEd25519", "value": identities[name]["priv"]}
        }
        priv_state = {"height": "0", "round": 0, "step": 0}
        
        write_json(os.path.join(node_dir, "node_key.json"), node_key)
        write_json(os.path.join(node_dir, "priv_validator_key.json"), priv_validator)
        write_json(os.path.join(node_dir, "priv_validator_state.json"), priv_state)
        
        log.info("  Generated native configs and keys for: %s", name)

    log.info("=== Config generation complete ===")

if __name__ == "__main__":
    try:
        generate_ssh_keys()
        generate_all()
    except Exception as e:
        log.error("Config generation failed: %s", e)
        raise
