# Script di configurazione della rete blockchain CometBFT (Evmos)
# Lanciato in fase di setup della rete, crea i file di configurazione della rete
# Usa evmosd testnet init-files per generare le configurazioni di validatori e nodi
# Genera i file necessari per ogni nodo della rete (validatori, light node, full node)

import base64
import hashlib
import io
import json
import logging
import re
import os
import subprocess
import shutil
import tarfile
import time
from Kathara.manager.Kathara import Kathara
from Kathara.model.Lab import Lab
from eth_utils import address as eth_address

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Blockchain-generator-cometbft")

CHAIN_ID = "evmos_9000-1"
NUM_VALIDATORS = 3
NUM_NON_VALIDATORS = 4
KEYRING_BACKEND = "test"
DENOM = "aevmos"

VALIDATOR_IPS = {
    0: "10.99.0.1",
    1: "10.99.0.2",
    2: "10.99.0.3",
}

NODE_IPS = {
    "light0": "10.99.0.11",
    "light1": "10.99.0.12",
    "light2": "10.99.0.13",
    "fullnode0": "10.99.0.14",
}

BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

def bech32_to_eth(bech32_addr):
    _, sep, data_part = bech32_addr.partition("1")
    if not sep:
        return bech32_addr
    data = [BECH32_CHARSET.index(c) for c in data_part[:-6]]
    acc = 0
    bits = 0
    result = []
    for value in data:
        acc = (acc << 5) | value
        bits += 5
        while bits >= 8:
            bits -= 8
            result.append((acc >> bits) & 0xFF)
    hex_addr = "0x" + bytes(result).hex()
    return eth_address.to_checksum_address(hex_addr)


def generate_blockchain_configurations():
    manager = Kathara.get_instance()

    logger.info("Pulizia di eventuali laboratori residui...")
    try:
        stale_lab = Lab("boot_lab_cometbft")
        manager.undeploy_lab(lab=stale_lab)
    except Exception:
        pass

    logger.info("Configurazione bootlab per la generazione delle configurazioni CometBFT...")
    boot_lab = Lab("boot_lab_cometbft")
    bootnode = boot_lab.new_machine("bootnode_cometbft", **{'image': 'kathara/cometbft'})

    init_cmd = (
        f"evmosd testnet init-files "
        f"--chain-id {CHAIN_ID} "
        f"--v {NUM_VALIDATORS} "
        f"--output-dir /tmp/testnet "
        f"--keyring-backend {KEYRING_BACKEND} "
        f"--minimum-gas-prices 0{DENOM}"
    )

    startup_script = f"""#!/bin/bash
OUT_DIR="/tmp/testnet"
mkdir -p $OUT_DIR
LOG="/tmp/evmosd_boot.log"

echo "evmosd version:" > $LOG
evmosd version >> $LOG 2>&1

echo "Inizializzazione manuale di 3 nodi..." >> $LOG
for i in 0 1 2; do
    NODE_DIR="$OUT_DIR/node$i/evmosd"
    mkdir -p $NODE_DIR
    echo "Init node$i..." >> $LOG
    evmosd init node$i --home $NODE_DIR --chain-id {CHAIN_ID} --log_level debug >> $LOG 2>&1
    # Add validator key
    echo "Add key node$i..." >> $LOG
    evmosd keys add node$i --home $NODE_DIR --keyring-backend test --key-type eth_secp256k1 >> $LOG 2>&1
done

# Raccolta degli indirizzi
ADDR0=$(evmosd keys show node0 --home $OUT_DIR/node0/evmosd --keyring-backend test -a)
ADDR1=$(evmosd keys show node1 --home $OUT_DIR/node1/evmosd --keyring-backend test -a)
ADDR2=$(evmosd keys show node2 --home $OUT_DIR/node2/evmosd --keyring-backend test -a)
echo "Addresses: $ADDR0, $ADDR1, $ADDR2" >> $LOG

echo "Aggiunta account di genesi..." >> $LOG
BASE_DIR="$OUT_DIR/node0/evmosd"
evmosd add-genesis-account $ADDR0 1000000000000000000000aevmos --home $BASE_DIR --log_level debug >> $LOG 2>&1
evmosd add-genesis-account $ADDR1 1000000000000000000000aevmos --home $BASE_DIR --log_level debug >> $LOG 2>&1
evmosd add-genesis-account $ADDR2 1000000000000000000000aevmos --home $BASE_DIR --log_level debug >> $LOG 2>&1

# Gentx richiede che gli account siano già presenti nel file di genesi.
# We use node0's genesis as the source of truth.
for i in 0 1 2; do
    NODE_DIR="$OUT_DIR/node$i/evmosd"
    cp $BASE_DIR/config/genesis.json $NODE_DIR/config/genesis.json
    echo "Gentx for node$i..." >> $LOG
    evmosd gentx node$i 100000000000000000000aevmos --home $NODE_DIR --keyring-backend test --chain-id {CHAIN_ID} --log_level debug >> $LOG 2>&1
    echo "Gentx node$i exit code: $?" >> $LOG
done

echo "Raccolta dei gentx..." >> $LOG
mkdir -p $BASE_DIR/config/gentx
cp $OUT_DIR/node0/evmosd/config/gentx/* $BASE_DIR/config/gentx/ 2>/dev/null
cp $OUT_DIR/node1/evmosd/config/gentx/* $BASE_DIR/config/gentx/ 2>/dev/null
cp $OUT_DIR/node2/evmosd/config/gentx/* $BASE_DIR/config/gentx/ 2>/dev/null
evmosd collect-gentxs --home $BASE_DIR --log_level debug >> $LOG 2>&1

echo "Patch del file di genesi: disabilitazione feemarket e rimozione limiti gas..." >> $LOG
apt-get update && apt-get install -y jq >> $LOG 2>&1
jq '.app_state.feemarket.params.no_base_fee = true | .app_state.feemarket.params.base_fee = "0" | .consensus_params.block.max_gas = "-1"' $BASE_DIR/config/genesis.json > /tmp/genesis.tmp
mv /tmp/genesis.tmp $BASE_DIR/config/genesis.json

echo "Distribuzione del file di genesi finale..." >> $LOG
cp $BASE_DIR/config/genesis.json $OUT_DIR/node1/evmosd/config/genesis.json
cp $BASE_DIR/config/genesis.json $OUT_DIR/node2/evmosd/config/genesis.json

echo "Generazione template di configurazione..." >> $LOG
evmosd init dummy --home /tmp/dummy_node --chain-id {CHAIN_ID} --keyring-backend test >> $LOG 2>&1
for i in 0 1 2; do
    NODE_DIR="$OUT_DIR/node$i/evmosd"
    cp /tmp/dummy_node/config/config.toml $NODE_DIR/config/config.toml
    cp /tmp/dummy_node/config/client.toml $NODE_DIR/config/client.toml
done

echo "ls -R output:" >> $LOG
ls -R $OUT_DIR >> $LOG 2>&1
touch /tmp/evmosd_done
"""

    boot_lab.create_startup_file_from_string(bootnode, startup_script)

    logger.info("Avvio del bootlab...")
    manager.deploy_lab(boot_lab)

    logger.info(f"Waiting for evmosd testnet init-files to complete...")
    max_wait = 120
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(5)
        elapsed += 5
        try:
            bootnode.api_object.get_archive('/tmp/evmosd_done')
            logger.info(f"Config generation completed after {elapsed}s")
            break
        except Exception:
            logger.info(f"Still waiting... ({elapsed}s/{max_wait}s)")
    else:
        logger.error("Timed out waiting for config generation")

    try:
        bits, stat = bootnode.api_object.get_archive('/tmp/evmosd_boot.log')
        raw = b''
        for chunk in bits:
            raw += chunk
        tf = tarfile.open(fileobj=io.BytesIO(raw))
        for member in tf.getmembers():
            f = tf.extractfile(member)
            if f:
                for line in f.read().decode('utf-8', errors='ignore').strip().split('\n'):
                    logger.info(f"boot.log: {line}")
    except Exception as e:
        logger.warning(f"Could not read boot log: {e}")

    logger.info("Scaricamento delle configurazioni sulla macchina host...")
    configurations_path = os.path.join("lab", "cometbft", "blockchain_config_tmp")
    if os.path.exists(configurations_path):
        shutil.rmtree(configurations_path)
    os.makedirs(configurations_path, exist_ok=True)

    tar_path = os.path.join(configurations_path, "testnet.tar")
    with open(tar_path, 'wb') as f:
        bits, stat = bootnode.api_object.get_archive('/tmp/testnet')
        for chunk in bits:
            f.write(chunk)

    logger.info("Archivio scaricato, estrazione in corso...")
    with tarfile.open(tar_path) as f:
        for member in f.getmembers():
            logger.info(f"Archive entry: {member.name}")
        f.extractall(path=configurations_path)
    os.remove(tar_path)

    logger.info("Rimozione del bootlab...")
    manager.undeploy_lab(lab=boot_lab)

    testnet_path = os.path.join(configurations_path, "testnet")
    return testnet_path




def generate_config(testnet_path, parameters):
    logger.info("Generazione file di configurazione CometBFT su disco...")
    contract_src_dir = os.path.abspath(os.path.join('lab', 'cometbft', 'shared', 'contract'))

    all_persistent_peers = build_persistent_peers(testnet_path)

    for i in range(NUM_VALIDATORS):
        node_name = f"validator{i}"
        src_dir = os.path.join(testnet_path, f"node{i}", "evmosd")
        target_dir = os.path.join("lab", "cometbft", node_name, "data")
        os.makedirs(target_dir, exist_ok=True)

        config_target = os.path.join(target_dir, "config")
        data_target = os.path.join(target_dir, "data")

        if os.path.exists(config_target):
            shutil.rmtree(config_target)
        if os.path.exists(data_target):
            shutil.rmtree(data_target)

        shutil.copytree(os.path.join(src_dir, "config"), config_target)
        shutil.copytree(os.path.join(src_dir, "data"), data_target)
        shutil.copytree(os.path.join(src_dir, "keyring-test"),
                        os.path.join(target_dir, "keyring-test"),
                        dirs_exist_ok=True)

        patch_config_toml(config_target, VALIDATOR_IPS[i], all_persistent_peers, i)
        patch_app_toml(config_target)

        contract_target = os.path.join(target_dir, "contract")
        if os.path.exists(contract_src_dir):
            if os.path.exists(contract_target):
                shutil.rmtree(contract_target)
            shutil.copytree(contract_src_dir, contract_target)

    for node_name, node_ip in NODE_IPS.items():
        target_dir = os.path.join("lab", "cometbft", node_name, "data")
        os.makedirs(target_dir, exist_ok=True)

        # Mappa lightX a nodeX per ottenere il keyring corretto
        match = re.search(r'light(\d+)', node_name)
        if match:
            idx = int(match.group(1))
            src_dir = os.path.join(testnet_path, f"node{idx % NUM_VALIDATORS}", "evmosd")
        else:
            src_dir = os.path.join(testnet_path, "node0", "evmosd")

        config_target = os.path.join(target_dir, "config")
        data_target = os.path.join(target_dir, "data")
        keyring_target = os.path.join(target_dir, "keyring-test")

        if os.path.exists(config_target): shutil.rmtree(config_target)
        if os.path.exists(data_target): shutil.rmtree(data_target)
        if os.path.exists(keyring_target): shutil.rmtree(keyring_target)

        # Copy base configuration
        shutil.copytree(os.path.join(src_dir, "config"), config_target)
        
        # IMPORTANTE: Rimuovere node_key.json e la chiave del validatore per evitare collisioni ID
        for f in ["node_key.json", "priv_validator_key.json"]:
            p = os.path.join(config_target, f)
            if os.path.exists(p): os.remove(p)

        # Pulizia directory dati
        os.makedirs(data_target)

        # Copia del keyring (contiene l'identità per le transazioni)
        shutil.copytree(os.path.join(src_dir, "keyring-test"), keyring_target)

        patch_config_toml(config_target, node_ip, all_persistent_peers, None)
        patch_app_toml(config_target)

        # Verifica coerenza del file di genesi
        genesis_src = os.path.join(testnet_path, "node0", "evmosd", "config", "genesis.json")
        genesis_dst = os.path.join(config_target, "genesis.json")
        shutil.copy(genesis_src, genesis_dst)

        contract_target = os.path.join(target_dir, "contract")
        if os.path.exists(contract_src_dir):
            if os.path.exists(contract_target):
                shutil.rmtree(contract_target)
            shutil.copytree(contract_src_dir, contract_target)

    configure_leader(
        os.path.join("lab", "cometbft", "validator0"),
        3,
        parameters,
        testnet_path
    )

    logger.info("CometBFT network configuration written to disk!")


def build_persistent_peers(testnet_path):
    peers = []
    for i in range(NUM_VALIDATORS):
        node_key_path = os.path.join(testnet_path, f"node{i}", "evmosd", "config", "node_key.json")
        with open(node_key_path) as f:
            node_key_data = json.load(f)

        node_id = node_key_data.get("id")
        if not node_id:
            # Calcolo manuale se l'ID è mancante in vecchie versioni
            priv_key_b64 = node_key_data["priv_key"]["value"]
            priv_key_bytes = base64.b64decode(priv_key_b64)
            pub_key_bytes = priv_key_bytes[32:]
            node_id = hashlib.sha256(pub_key_bytes).hexdigest()[:40]

        ip = VALIDATOR_IPS[i]
        peer = f"{node_id}@{ip}:26656"
        logger.info(f"Node {i} P2P Address: {peer}")
        peers.append(peer)

    return ",".join(peers)


def patch_config_toml(config_dir, listen_ip, persistent_peers, validator_idx):
    config_path = os.path.join(config_dir, "config.toml")
    with open(config_path, 'r') as f:
        content = f.read()

    content = content.replace(
        'laddr = "tcp://0.0.0.0:26656"',
        f'laddr = "tcp://0.0.0.0:26656"'
    )
    content = content.replace(
        'laddr = "tcp://127.0.0.1:26657"',
        f'laddr = "tcp://0.0.0.0:26657"'
    )

    content = re.sub(
        r'persistent_peers = ".*?"',
        f'persistent_peers = "{persistent_peers}"',
        content
    )
    
   # Ottimizzazione timeout del consenso per carichi massivi (Stress Test)
    content = re.sub(r'timeout_propose = ".*?"', 'timeout_propose = "300ms"', content)
    content = re.sub(r'timeout_propose_delta = ".*?"', 'timeout_propose_delta = "200ms"', content)
    content = re.sub(r'timeout_prevote = ".*?"', 'timeout_prevote = "300ms"', content)
    content = re.sub(r'timeout_prevote_delta = ".*?"', 'timeout_prevote_delta = "200ms"', content)
    content = re.sub(r'timeout_precommit = ".*?"', 'timeout_precommit = "300ms"', content)
    content = re.sub(r'timeout_precommit_delta = ".*?"', 'timeout_precommit_delta = "200ms"', content)
    
    content = re.sub(r'timeout_commit = ".*?"', 'timeout_commit = "500ms"', content)
    
    content = re.sub(r'skip_timeout_commit = .*', 'skip_timeout_commit = true', content)
    content = re.sub(r'peer_gossip_sleep_duration = ".*?"', 'peer_gossip_sleep_duration = "100ms"', content)
    content = re.sub(r'recheck = .*', 'recheck = true', content)
    
    # CRITICO 2: Aumenta il timeout per l'RPC prima che scarti le connessioni HTTP
    if 'timeout_broadcast_tx_commit' in content:
        content = re.sub(r'timeout_broadcast_tx_commit = ".*?"', 'timeout_broadcast_tx_commit = "5s"', content)
    else:
        # Se non c'è, lo aggiungiamo sotto [rpc]
        content = content.replace('[rpc]', '[rpc]\ntimeout_broadcast_tx_commit = "5s"')

    # CRITICO 3: Previeni la TCP Starvation alzando il limite di connessioni simultanee
    content = re.sub(r'max_open_connections = 900', 'max_open_connections = 4000', content)
    
    # Disabilitazione generazione blocchi vuoti
    content = content.replace('create_empty_blocks = true', 'create_empty_blocks = false')
    
    # Ottimizzazione mempool per carichi elevati
    content = re.sub(r'size = 5000', 'size = 50000', content)
    content = re.sub(r'cache_size = 10000', 'cache_size = 100000', content)

    if validator_idx is None:
        content = content.replace('mode = "validator"', 'mode = "full"')

    with open(config_path, 'w') as f:
        f.write(content)


def patch_app_toml(config_dir):
    app_path = os.path.join(config_dir, "app.toml")
    if not os.path.exists(app_path):
        return
    with open(app_path, 'r') as f:
        content = f.read()

    content = content.replace('enable = false', 'enable = true', 1)
    content = content.replace(
        'address = "127.0.0.1:8545"',
        'address = "0.0.0.0:8545"'
    )
    content = content.replace(
        'ws-address = "127.0.0.1:8546"',
        'ws-address = "0.0.0.0:8546"'
    )
    content = content.replace(
        'minimum-gas-prices = ""',
        f'minimum-gas-prices = "0{DENOM}"'
    )

    with open(app_path, 'w') as f:
        f.write(content)


def configure_leader(node_path, members_for_group, parameters, testnet_path):
    target_dir = os.path.join(node_path, "data", "contract")
    ids_path = os.path.join(target_dir, 'IDS.sol')

    if not os.path.exists(ids_path):
        logger.warning(f"IDS.sol not found in {target_dir}")
        return

    with open(ids_path, 'r') as f:
        content = f.read()

    light_accounts = []
    for i in range(NUM_VALIDATORS):
        keyring_dir = os.path.join(testnet_path, f"node{i}", "evmosd", "keyring-test")
        if os.path.exists(keyring_dir):
            for fname in os.listdir(keyring_dir):
                if fname.endswith(".address"):
                    # In evmosd v20+, .address files contain protobuf data.
                    # The hex address is encoded in the filename itself
                    hex_addr = fname.replace(".address", "")
                    addr = eth_address.to_checksum_address("0x" + hex_addr)
                    logger.info(f"Node {i} address: {addr} (from filename {fname})")
                    light_accounts.append(addr)
                    break

    while len(light_accounts) < 3:
        light_accounts.append("0x0000000000000000000000000000000000000000")

    agent_ids = ", ".join(light_accounts[:3])
    params_formatted = ", ".join(f'"{p}"' for p in parameters)

    content = content.replace("#AGENTS", agent_ids) \
                     .replace("#PARAMS", params_formatted) \
                     .replace("#NUMAGENTS4PARAMS", str(members_for_group))

    with open(ids_path, 'w') as f:
        f.write(content)


def generate_ssh_keys():
    ssh_dir = os.path.join("lab", "cometbft", "shared", "ssh")
    key_path = os.path.join(ssh_dir, "actuator_id_ed25519")

    if os.path.exists(key_path):
        logger.info("SSH keys already exist, skipping generation.")
        return

    os.makedirs(ssh_dir, exist_ok=True)
    logger.info("Generating SSH keys for actuator...")
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", "", "-C", "actuator@sps"],
        check=True
    )
    logger.info(f"SSH keys generated in {ssh_dir}")


if __name__ == "__main__":
    try:
        generate_ssh_keys()
        testnet_path = generate_blockchain_configurations()
        generate_config(testnet_path, ["SQL_INJECTION", "XSS_ATTACK", "PATH_TRAVERSAL", "COMMAND_INJECTION"])
    except Exception as e:
        logger.error(f"Failed to generate configuration: {e}")
        raise e