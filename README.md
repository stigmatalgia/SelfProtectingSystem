# SPS-Blockchain
> **Self-Protecting System Architecture for Cyber-Resilience**
> *Based on the paper: "A Novel Architecture for Cyber-Resilient SPS Based on Blockchain"*
---
## Overview
The **SPS-Blockchain** network is a simulated cyber-resilient environment deployed using Kathara. It implements an autonomous, self-protecting system that leverages an immutable, distributed ledger (Quorum QBFT) to securely and reliably manage Intrusion Detection System (IDS) alerts and trigger subsequent mitigation actions on target systems.
It is assumed that the containers are unescapable and that the performed action certainly mitigates the detected intrusion.

## ISO/OSI Level 3 architecture

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
│ member3          │                          │ member0        │ │ member1        │ │ member2        │  
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

3. **Alert Generation**: Upon detecting a malevolent request, the IDSs generate alerts via `alert_sender.sh` and forward them via HTTP POST requests to their respective blockchain proxy nodes (**Member 0, 1, and 2**).

4. **Blockchain Transaction**: The Member nodes convert these HTTP requests into blockchain transactions via `blockchain_api.js`, submitting the alerts securely to the `IDS.sol` Smart Contract. 

5. **Consensus & Evaluation**: The **Validator** nodes process the transactions using the **QBFT consensus** algorithm. The Smart Contract registers the attack state and, upon reaching the consensus of 2/3 of the validators, it emits an `ActionRequired` event.

6. **Actuation Forwarding**: **Member 3** monitors the blockchain for new events using `actuator_forwarder.js`. When it detects `ActionRequired`, it forwards an execution payload to the **Actuator**.

7. **Self-protection**: The **Actuator** executes the action associated with the mitigation of the detected attack directly on the target (**Juice Shop**) via SSH.

---

## Installation & Usage

### Prerequisites
*   [Docker](https://www.docker.com/) 
*   [Kathara](https://www.kathara.org/) 
*   Python 3 & Virtual Environment Module

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd SelfProtectingSystemDemo
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
   make setup
   #To test the loop by yourself
   #TODO: Add more signatures, currently only SQL Injection is detectable
   kathara exec attacker -- curl 'http://10.0.0.80:3000/rest/products/search?q=1=1'
   
   ```
4. **Clean up the temp files and stop the lab:**
   ```bash
   make clean-config
   ```

