# SPS-Blockchain STILL A PROTOTYPE
> **Self-Protecting System Architecture for Cyber-Resilience**
> *Based on the paper: "A Novel Architecture for Cyber-Resilient SPS Based on Blockchain"*

## Architecture
```ascii
                                            ┌───────────────┐
                                            │   ATTACKER    │
                                            └───────┬───────┘
                                                    │
                                            ┌───────▼───────┐
                                            │    ROUTER     │
                                            └───────┬───────┘
                                                    │ (Untrusted network)
           ┌────────────────────────────────────────┴────────────────────────────────────────┐
           │                                      DMZ                                        │
           │                          ┌─────────────────────────┐                            │
           │                          │  MANAGED SYSTEM (Target)│                            │
           │                          │  App: OWASP Juice Shop  │                            │
           │                          │  IP:  10.0.0.80         │                            │
           │                          └─────────────────────────┘                            │
           │                                       ▲                                         │
           │                                       │ (Traffic Mirrored by the router)        │
           │            ┌──────────────────────────┼──────────────────────────┐              │
           │      ┌─────▼─────┐              ┌─────▼─────┐              ┌─────▼─────┐        │
           │      │ IDS Node 1│              │ IDS Node 2│              │ IDS Node 3│        │
           │      │   Snort   │              │ Suricata  │              │   Zeek    │        │
           │      └─────┬─────┘              └─────┬─────┘              └─────┬─────┘        │
           │            │                          │                          │              │
           └────────────┼──────────────────────────┼──────────────────────────┼──────────────┘
                        │                          │                          │
           ┌────────────▼──────────────────────────▼──────────────────────────▼──────────────┐
           │                                MANAGER NETWORK                                  │
           │      ┌────────────┐             ┌────────────┐             ┌────────────┐       │
           │      │ Validator 1│◄───────────►│ Validator 2│◄───────────►│ Validator 3│       │
           │      │ (Leader)   │             │            │             │            │       │
           │      └────────────┘             └────────────┘             └────────────┘       │
           │      ┌─────────────────────────────────────────────────────────────────────┐    │
           │      │                   SMART CONTRACT (IDS.sol)                          │    │
           │      │  • QBFT Consensus (3 validators)                                    │    │
           │      │                                                                     │    │
           │      └─────────────────────────────────────────────────────────────────────┘    │
           │                                                                                 │
           └─────────────────────────────────────────────────────────────────────────────────┘
```

## Overview

The network architecture is implemented in kathara' using docker containers.

The blockchain setup is performed by generate_blockchain_config.py script, which generates the necessary configuration files for the validators.
(TODO: Refactor, it actually is a SLIGHTLY adapted version of the one provided by the paper documentation)

The attacker machine is a user that can access the untrusted network and send potentially malicious requests to the JUICE SHOP (i also added carbonyl browser to perform attacks graphically, not useful at all but very cool)

When an attack from the untrusted network is detected, the IDSs send alerts using HTTP requests to the validator nodes.
Each IDS container sends an alert with the following format:
```json
{
    "ids": "IDS_NAME",
    "message": "ALERT_MESSAGE",
    "severity": "ALERT_SEVERITY",
    "type": "ALERT_TYPE",
    "timestamp": "ALERT_TIMESTAMP"
}
```
An alert is sent every time the IDS process detects an attack and successfully writes it into a log file.
The log file is monitored using tail command.

Each validator node hosts a blockchain API that receives the alerts and submits them as transactions to the blockchain.

TO BE IMPLEMENTED: Actuator endpoint to trigger a system state change.

**TODO**: Ill write a decent setup guide after trying it on other machines...