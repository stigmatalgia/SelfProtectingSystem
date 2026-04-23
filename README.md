# SPS-Blockchain
> **Self-Protecting System Architecture for Cyber-Resilience**
> *Based on the paper: "A Novel Architecture for Cyber-Resilient SPS Based on Blockchain"*
---
## Overview
The **SPS-Blockchain** network is a simulated cyber-resilient environment deployed using Kathara. It implements an autonomous, self-protecting system that leverages an immutable, distributed ledger (Quorum QBFT or CometBFT) to securely and reliably manage Intrusion Detection System (IDS) alerts and trigger subsequent mitigation actions on target systems.
It is assumed that the containers are unescapable and that the performed action certainly mitigates the detected intrusion.

## ISO/OSI Level 3 QUORUM/COMETBFT architecture

```text
    ╭───────────────────────────────────╮
    │    UNTRUSTED ZONE / INTERNET      │                                                               
    │        (192.168.1.0/24)           │                                                               
    ╰────────────────┬──────────────────╯                                                               
                     │                                                                                  
             ╭───────┴────────╮                                                                         
             │ attacker       │                                                                         
             │ eth0           │                                                                         
             │ 192.168.1.10   │                                                                         
             ╰───────┬────────╯                                                                         
                     │                                                                                  
             ╭───────┴────────╮                                                                         
             │ router         │                                                                         
             │ eth0           │                                                                         
             │ 192.168.1.254  │                                                                         
             ╰───────┬────────╯       (sniffing)                                                        
   eth1 (10.0.0.254) ├──────────────────────────────────┬────────────────┬────────────────┐             
                     │                                  │                │                │             
╭────────────────────┴────────────────────────╮ ╭───────┴────────╮ ╭─────┴──────────╮ ╭───┴────────────╮
│                DMZ NETWORK                  │ │ ids_snort      │ │ ids_suricata   │ │ ids_zeek       │
│                10.0.0.0/24                  │ │ eth1 (promisc) │ │ eth1 (promisc) │ │ eth1 (promisc) │
╰────────┬────────────────────┬───────────────╯ ╰─────┬──────────╯ ╰───┬────────────╯ ╰─┬──────────────╯
         │                    │                       │                │                │               
  ╭──────┴─────────╮   ╭──────┴─────────╮             │   eth0         │   eth0         │   eth0        
  │ actuator       │   │ juice_shop     │             │   172.16.1.1   │   172.16.2.1   │   172.16.3.1  
  │ eth1           │   │ eth0           │             │                │                │               
  │ 10.0.0.10      │   │ 10.0.0.80      │             │                │                │               
  ╰──────┬─────────╯   ╰────────────────╯     ╭───────┴────────╮ ╭─────┴──────────╮ ╭───┴────────────╮  
         │ eth0                               │   mgmt1 net    │ │   mgmt2 net    │ │   mgmt3 net    │  
         │ 172.16.4.1                         │  172.16.1.0/24 │ │  172.16.2.0/24 │ │  172.16.3.0/24 │  
         │                                    ╰───────┬────────╯ ╰─────┬──────────╯ ╰───┬────────────╯  
╭────────┴─────────╮                                  │                │                │               
│  Actuator Net    │                                  │                │                │               
│  172.16.4.0/24   │                                  │                │                │               
╰────────┬─────────╯                                  │                │                │               
         │                                            │                │                │               
╭────────┴─────────╮                          ╭───────┴────────╮ ╭─────┴──────────╮ ╭───┴────────────╮  
│ member3/fullnode0│                          │ member0/light0 │ │ member1/light1 │ │ member2/light2 │  
│ eth0             │                          │ eth0           │ │ eth0           │ │ eth0           │  
│ 172.16.4.10      │                          │ 172.16.1.10    │ │ 172.16.2.10    │ │ 172.16.3.10    │  
╰────────┬─────────╯                          ╰───────┬────────╯ ╰─────┬──────────╯ ╰───┬────────────╯  
         │ eth1                                       │ eth1           │ eth1           │ eth1          
         │ 10.99.0.14                                 │ 10.99.0.11     │ 10.99.0.12     │ 10.99.0.13    
         │                                            │                │                │               
╭────────┴────────────────────────────────────────────┴────────────────┴────────────────┴───────────╮   
│                                     BLOCKCHAIN NETWORK                                            │   
│                                        10.99.0.0/24                                               │   
╰────────┬──────────────────────────────────────┬─────────────────────────────────────────┬─────────╯   
         │                                      │                                         │             
 ╭───────┴────────╮                     ╭───────┴────────╮                        ╭───────┴────────╮    
 │ validator0     │                     │ validator1     │                        │ validator2     │    
 │ eth0           │                     │ eth0           │                        │ eth0           │    
 │ 10.99.0.1      │                     │ 10.99.0.2      │                        │ 10.99.0.3      │    
 ╰────────────────╯                     ╰────────────────╯                        ╰────────────────╯              
```

## The System Loop

1. **Attack Injection**: The **Attacker** launches malicious requests toward the **Juice Shop**

2. **Traffic Mirroring & Detection**: The **Router** mirrors the incoming traffic to the three different IDSs (**Snort**, **Suricata**, **Zeek**) running in promiscuous mode.

3. **Alert Generation**: Upon detecting a malevolent request, the IDSs generate alerts via `alert_sender.sh` and forward them via HTTP POST requests to their respective blockchain proxy nodes (**Member/Light 0, 1, and 2**).

4. **Blockchain Transaction**: The Member nodes convert these HTTP requests into blockchain transactions via `blockchain_api.js`, submitting the alerts securely to the `IDS.sol` Smart Contract. 

5. **Consensus & Evaluation**: The **Validator** nodes process the transactions using the **QBFT consensus** algorithm. The Smart Contract registers the attack state and, upon reaching the consensus of 2/3 of the validators, it emits an `ActionRequired` event.

6. **Actuation Forwarding**: **Member 3/Fullnode 0** monitors the blockchain for new events using `actuator_forwarder.js`. When it detects `ActionRequired`, it forwards an execution payload to the **Actuator**.

7. **Self-protection**: The **Actuator** executes the action associated with the mitigation of the detected attack directly on the target (**Juice Shop**) via SSH.

---

## Dual Blockchain Environments

This project supports two different consensus engines to power the blockchain layer: **Quorum (QBFT)** and **CometBFT**.
While the overall defensive system architecture remains identical, the underlying network topology may slightly vary:

*   **Quorum (QBFT)**: Utilizes **Member Nodes** as API gateways for incoming IDS alerts (Member 0, 1, 2) and for actuation monitoring (Member 3). Three **Validator Nodes** ensure consensus on the network.
*   **CometBFT**: Replaces Member nodes with **Light Nodes** (Light 0, 1, 2) to ingest HTTP alerts, and uses a **Full Node** (Fullnode 0) to monitor the blockchain state and dispatch the execution payload to the Actuator. The network is secured by three **Validator Nodes** running the CometBFT consensus protocol.

## Benchmarking the System

The repository includes a little benchmarking suite designed to measure the system's end-to-end response delay—calculated as the exact delta between the moment an IDS detects an intrusion and the instant the Actuator executes the mitigation script.

*   **`measure_response_time.py`**: Parses the logs of Snort, Suricata, Zeek, and the Actuator to determine the first synchronized detection and its corresponding mitigation, returning the isolated delta in seconds.
*   **`generate_chart.py`**: Automates multiple sequential attack simulations (default `N=10`), records the response time for each iteration, and generates a Boxplot chart to visually evaluate the system's performance and cyber-resilience under stress.

Results are saved as JSON data and PNG charts within the `benchmark/result/<lab_type>/` directory.

---

## Installation & Usage

### Prerequisites
*   Docker
*   Kathara 
*   Python 3 & Virtual Environment Module

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd SelfProtectingSystem
   ```

2. **Set up the Python Virtual Environment:**
   I strongly suggest to use a virtual environment to manage dependencies since some of the required packages are using old versions of setuptools and wheel that may conflict with other packages installed on your system.
   
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Build the docker images and start the simulation:**
   ```bash
   #BEWARE, THE STARTUP CAN TAKE UP TO 1/2 MINUTES
   
   # To start the Quorum environment (Default)
   make quorum setup

   # To start the CometBFT environment
   make cometbft setup
   ```

3. **Clean up the temp files and stop the lab:**
   ```bash
   make clean-config
   ```

4. **Run the Benchmark Suite:**
   You can easily run the entire suite of benchmarks on both Quorum and CometBFT sequentially using a single command. This will automatically setup, benchmark, and clean the environments, and finally generate all comparative performance charts in the `benchmark/result/` folder:
   ```bash
   make all-benchmarks
   ```

   *Alternatively, you can run individual benchmark commands on a running lab:*
   ```bash
   
   # Generate sequential attack boxplot data (e.g., 10 attacks)
   make <quorum|cometbft> chart N=10
   
   # Run system capacity and throughput test
   make <quorum|cometbft> capacity
   
   # Run native blockchain transaction test
   make <quorum|cometbft> blockchain-benchmark
   ```

**EXTRA, Testing commands!**

```bash
kathara exec attacker -- curl 'http://10.0.0.80:3000/rest/products/search?q=1=1'
kathara exec attacker -- curl 'http://10.0.0.80:3000/rest/products/search?q=<script>alert(1)</script>'
kathara exec attacker -- curl 'http://10.0.0.80:3000/rest/products/search?q=cat+/etc/passwd'
kathara exec attacker -- curl --path-as-is 'http://10.0.0.80:3000/public/images/../../../../'

# Only Quorum!
kathara exec member3 -- sh -c "geth attach /home/qbft/data/geth.ipc --exec \"var addr = '$(cat shared/contract_address.txt | tr -d '[:space:]')'; var abi = [{'name':'statusMapDT','type':'function','inputs':[{'type':'string'}],'outputs':[{'type':'uint256'}]}]; var c = eth.contract(abi).at(addr); JSON.stringify({sql: c.statusMapDT.call('SQL_INJECTION').toNumber(), xss: c.statusMapDT.call('XSS_ATTACK').toNumber(), path: c.statusMapDT.call('PATH_TRAVERSAL').toNumber(), cmd: c.statusMapDT.call('COMMAND_INJECTION').toNumber()})\""
```