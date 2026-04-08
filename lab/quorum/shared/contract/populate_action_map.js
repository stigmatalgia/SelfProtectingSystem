// In uso da validator0, guarda file di startup
const fs = require('fs');
const { Web3 } = require('web3');
const { IpcProvider } = require('web3-providers-ipc');
const ADDR_FILE = '/home/qbft/data/contract_address.txt';
const ABI_FILE = '/home/qbft/data/contract_abi.json';
const IPC_PATH = '/home/qbft/data/geth.ipc';

/* Seguendo la logica del contratto in IDS.sol
   Ogni stato corrisponde ad un codice binario di 4 bit
   dove ogni bit rappresenta la presenza di un attacco
   0 = attacco non rilevato
   1 = attacco rilevato
*/
const BASE_ACTIONS = [
    { bit: 8, state: "1000", action: "echo SQL Injection detected" },
    { bit: 4, state: "0100", action: "echo XSS Attack detected" },
    { bit: 2, state: "0010", action: "echo Path Traversal detected" },
    { bit: 1, state: "0001", action: "echo Command Injection detected" },
];
// Crea gli stati intermedi e le azioni concatenate
function generatePolicy() {
    const policy = [];

    for (let i = 1; i <= 15; i++) {
        const stateStr = i.toString(2).padStart(4, '0');
        const chainedActions = [];
        for (const base of BASE_ACTIONS) {
            if ((i & base.bit) === base.bit) {
                chainedActions.push(base.action);
            }
        }

        policy.push({
            state: stateStr,
            action: chainedActions.join(' && ')
        });
    }

    return policy;
}

const POLICY = generatePolicy();

async function populate() {
    if (!fs.existsSync(IPC_PATH)) {
        console.error("Geth IPC not found");
        process.exit(1);
    }

    const web3 = new Web3(new IpcProvider(IPC_PATH));

    try {
        const accounts = await web3.eth.getAccounts();
        const account = accounts[0];

        if (!fs.existsSync(ADDR_FILE) || !fs.existsSync(ABI_FILE)) {
            console.error("Contract artifacts not found");
            process.exit(1);
        }

        const abi = JSON.parse(fs.readFileSync(ABI_FILE, 'utf8'));
        const address = fs.readFileSync(ADDR_FILE, 'utf8').trim();
        const contract = new web3.eth.Contract(abi, address);

        console.log(`Populating map for contract at ${address}...`);
        console.log(`Uploading ${POLICY.length} state configurations.`);

        const states = POLICY.map(p => p.state);
        const actions = POLICY.map(p => p.action);

        const tx = await contract.methods.insertMap(states, actions).send({
            from: account,
            gas: 2000000,
            gasPrice: 0,
            type: '0x0'
        });

        console.log(`Successfully populated map. TX: ${tx.transactionHash}`);
    } catch (e) {
        console.error("Error populating map:", e);
    } finally {
        web3.provider.disconnect();
    }
}

populate();