# Homunculus script di configurazione della rete blockchain
# Lanciato in fase di setup della rete, crea i file di configurazione della rete permissioned
# Si occupa di evitare di fare esplorazione dei nodi creando indirizzi e specificando tutti
# i tweak necessari a usare la blockchain evitando la logica non necessaria (gas infinito)
# Determina il block period della rete sia quando sono presenti transazioni che quando non
# usa due parametri diversi perche' permette di non generare traffico inutile
# TODO: pesante refactoring

import ipaddress
import json
import logging
import os
import subprocess
import tarfile
import shutil
import sys
import io
import time

# Protezione contro errori di codifica in ambienti Kathara
if sys.stdout.encoding is None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from Kathara.manager.Kathara import Kathara
from Kathara.model.Lab import Lab
from eth_utils import address as eth_address

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Blockchain-generator")

def generate_blockchain_configurations():
    num_validators = 3
    num_members = 4
    consensus = 'qbft'
    blocktime = 1
    manager = Kathara.get_instance()

    logger.info("Configuring bootlab for generating nodes' configurations...")
    boot_lab = Lab("boot_lab")
    bootnode = boot_lab.new_machine("bootnode", **{'image': 'kathara/quorum'})

    boot_lab.create_startup_file_from_string(
        bootnode,
        # quorum-genesis-tool is baked into the image at
        # /home/qbft/node_modules; invoking it through bare `npx` from /
        # forces a registry lookup that fails on isolated Kathara networks.
        f"cd /home/qbft && ./node_modules/.bin/quorum-genesis-tool "
        f"--consensus {consensus} --chainID 2222 --emptyblockperiodseconds 600 --blockperiod {blocktime} "
        " --difficulty 1 --gasLimit '0xFFFFFFFFF' --isQuorum --coinbase '0x0000000000000000000000000000000000000000' "
        f"--validators {num_validators} --members {num_members} --bootnodes 0 --outputPath '/lab/quorum/shared'"
    )

    deployed = False
    try:
        logger.info("Deploying bootlab...")
        manager.deploy_lab(boot_lab)
        deployed = True

        # Wait for quorum-genesis-tool to actually produce its output.
        # The tool may need to be fetched by npx on first use, which can take
        # a while on cold caches — grabbing the archive immediately used to
        # race against it and fail with "Could not find /lab/quorum/shared".
        logger.info("Waiting config generation...")
        # quorum-genesis-tool nests its artifacts under a timestamped
        # directory and populates it incrementally: waiting for the FIRST
        # directory made the download race against key generation, yielding
        # truncated archives. Wait until every expected artifact exists.
        n_val_memb = num_validators + num_members
        probe = (
            "D=$(ls -d /lab/quorum/shared/*/ 2>/dev/null | head -1); "
            f"[ -n \"$D\" ] && "
            f"[ \"$(ls -d $D/validator* $D/member* 2>/dev/null | wc -l)\" -ge {n_val_memb} ] && "
            "[ -s \"$D/goQuorum/permissioned-nodes.json\" ] && echo GEN_READY"
        )
        tar_path = os.path.join(".", "lab", "quorum", "shared.tar")
        ready = False
        for _ in range(120):  # up to ~4 minutes
            stdout, _, _ = manager.exec_obj(
                bootnode,
                ["sh", "-c", probe],
                stream=False,
            )
            if stdout is None:
                time.sleep(2)
                continue
            listing = stdout.decode() if isinstance(stdout, bytes) else str(stdout)
            if "GEN_READY" in listing:
                ready = True
                # Give the tool a final moment to flush, then download
                # immediately: the artifacts live on the ephemeral machine
                # filesystem.
                time.sleep(2)
                logger.info("Downloading configurations on the host machine...")
                with open(tar_path, 'wb') as f:
                    bits, stat = bootnode.api_object.get_archive('/lab/quorum/shared')
                    for chunk in bits:
                        f.write(chunk)
                break
            time.sleep(2)
        if not ready:
            raise RuntimeError(
                "quorum-genesis-tool did not produce output in /lab/quorum/shared "
                "(timeout). Inspect the bootnode logs for details."
            )
    finally:
        logger.info("Undeploying bootlab...")
        manager.undeploy_lab(lab=boot_lab)
    
    # Keep generated material INSIDE the repository (the old
    # ../resources/ path escaped the project root and broke portability).
    configurations_path = os.path.join("lab", "quorum", "generated_configurations")
    if os.path.exists(configurations_path):
        shutil.rmtree(configurations_path)
    os.makedirs(configurations_path, exist_ok=True)
    
    # Estrazione dell'archivio e pulizia file temporanei
    with tarfile.open(tar_path) as f:
        f.extractall(path=configurations_path)
    os.remove(tar_path)
    
    generated_configurations_path = os.path.join(configurations_path, "shared")
    # Search for the directory containing goQuorum (usually a timestamped folder)
    for root, dirs, _ in os.walk(generated_configurations_path):
        if "goQuorum" in dirs:
            return root
    
    # Fallback to the first subdirectory if goQuorum is not found
    subdirs = [os.path.join(generated_configurations_path, d) for d in os.listdir(generated_configurations_path) 
               if os.path.isdir(os.path.join(generated_configurations_path, d))]
    if subdirs:
        return subdirs[0]
        
    return generated_configurations_path


def generate_config(configurations_path: str, parameters: list[str]):
    members_for_group = 3
    logger.info("Generating configuration files on disk...")
    
    all_node_dirs = [d for d in os.listdir(configurations_path) 
                     if d.startswith("validator") or d.startswith("member")]
    sorted_nodes = sorted(all_node_dirs, key=lambda item: (
        0 if 'validator' in item else 1,
        int(next(filter(lambda x: str.isdigit(x), item)))
    ))
    with open(os.path.join(configurations_path, "goQuorum", "permissioned-nodes.json")) as nodes_file:
        permissioned_nodes_template = json.load(nodes_file)
    
    final_enode_list = []
    node_metadata = {} 
    logger.info("Processing nodes information and building Full Mesh...")
    for node_name in sorted_nodes:
        node_key_path = os.path.join(configurations_path, node_name, "nodekey.pub")
        with open(node_key_path, "r") as f:
            raw_nodekey = f.readline().strip()
            clean_nodekey = raw_nodekey.replace("0x", "")
            node_id_suffix = clean_nodekey[-128:] 
            
        address_path = os.path.join(configurations_path, node_name, "address")
        with open(address_path, "r") as f:
            addr = f.readline().strip()
            
        found_enode = None
        for template_enode in permissioned_nodes_template:
            if node_id_suffix in template_enode:
                found_enode = template_enode
                break
        
        if found_enode:
            node_ip = "127.0.0.1"
            if "validator" in node_name:
                try:
                    idx = int(''.join(filter(str.isdigit, node_name)))
                    node_ip = f"10.99.0.{idx + 1}"
                except ValueError:
                    logger.warning(f"Could not parse index from {node_name}, using default IP")
            elif "member" in node_name:
                try:
                    idx = int(''.join(filter(str.isdigit, node_name)))
                    node_ip = f"10.99.0.{idx + 11}"
                except ValueError:
                    logger.warning(f"Could not parse index from {node_name}, using default IP")
            
            final_enode_url = found_enode.replace('<HOST>', node_ip).replace('0.0.0.0', node_ip)
            final_enode_list.append(final_enode_url)
        else:
            logger.error(f"Could not find enode match for {node_name} with key ending in {node_id_suffix}")
            
        node_metadata[node_name] = {
            "address": eth_address.to_checksum_address(addr),
            "ip": "0.0.0.0" 
        }
        
    logger.info(f"Generated {len(final_enode_list)} static nodes entries.")
    
    # Definizione del percorso dei sorgenti del contratto
    contract_src_dir = os.path.abspath(os.path.join('lab', 'quorum', 'shared', 'contract'))
    
    for node_name in sorted_nodes:
        logger.info(f"Configuring node {node_name}...")
        
        target_dir = os.path.join("lab", "quorum", node_name, "data")
        os.makedirs(target_dir, exist_ok=True)
        
        with open(os.path.join(target_dir, 'permissioned-nodes.json'), 'w') as f:
            json.dump(final_enode_list, f, indent=4)
            
        with open(os.path.join(target_dir, 'static-nodes.json'), 'w') as f:
            json.dump(final_enode_list, f, indent=4)
            
        shutil.copy(os.path.join(configurations_path, "goQuorum", "genesis.json"), 
                    os.path.join(target_dir, "genesis.json"))
                    
        node_config_path = os.path.join(configurations_path, node_name)
        keystore_dir = os.path.join(target_dir, 'keystore')
        os.makedirs(keystore_dir, exist_ok=True)
        
        for conf_file in os.listdir(node_config_path):
            src_path = os.path.join(node_config_path, conf_file)
            if 'account' in conf_file:
                dst_path = os.path.join(keystore_dir, conf_file)
            else:
                dst_path = os.path.join(target_dir, conf_file)
            if os.path.isfile(src_path):
                shutil.copy(src_path, dst_path)
                
        contract_target_dir = os.path.join(target_dir, "contract")
        if os.path.exists(contract_src_dir):
            if os.path.exists(contract_target_dir):
                shutil.rmtree(contract_target_dir) 
            shutil.copytree(contract_src_dir, contract_target_dir)
        else:
            logger.warning(f"Contract source directory {contract_src_dir} not found. Skipping copy.")
            
    configure_leader(os.path.join("lab", "quorum", "validator0"), members_for_group, parameters, node_metadata)
    logger.info("Network configuration written to disk!")


def configure_leader(node_path, members_for_group, parameters, node_metadata):
    target_dir = os.path.join(node_path, "data", "contract")
    ids_path = os.path.join(target_dir, 'IDS.sol')
    
    if not os.path.exists(ids_path):
        logger.warning(f"IDS.sol not found in {target_dir}")
        return
        
    with open(ids_path, 'r') as f:
        content = f.read()
        
    members = [meta['address'] for name, meta in node_metadata.items() if "member" in name]
    ids_members = sorted([name for name in node_metadata.keys() if "member" in name])[:3]
    agent_ids = ", ".join(node_metadata[name]['address'] for name in ids_members)
    
    params_formatted = ", ".join(f'"{p}"' for p in parameters)
    
    contract_templ = content.replace("#AGENTS", agent_ids) \
                               .replace("#PARAMS", params_formatted) \
                               .replace("#NUMAGENTS4PARAMS", str(members_for_group))
            
    with open(ids_path, 'w') as f:
        f.write(contract_templ)


def generate_ssh_keys():
    # Generazione chiavi SSH per l'attuatore
    ssh_dir = os.path.join("lab", "quorum", "shared", "ssh")
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
        config_path = generate_blockchain_configurations()
        # Only pass the 4 attack types as state parameters, not SAFE_ENVIRONMENT
        # SAFE_ENVIRONMENT is a control signal handled separately, not part of the state vector
        generate_config(config_path, ["SQL_INJECTION", "XSS_ATTACK", "PATH_TRAVERSAL", "COMMAND_INJECTION"])
    except Exception as e:
        logger.error(f"Failed to generate configuration: {e}")
        raise e