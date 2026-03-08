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
   Da implementare potenzialmente la concatenazione di più attacchi
   facendo la somma dei bit e concatenazione dei comandi
*/
const POLICY = [
    { state: "1000", action: "echo SQL Injection detected" },
    { state: "0100", action: "echo XSS Attack detected" },
    { state: "0010", action: "echo Path Traversal detected" },
    { state: "0001", action: "echo Brute Force detected" },
];

async function populate() {
    if (!fs.existsSync(IPC_PATH)) {
        console.error("Geth IPC not found");
        process.exit(1);
    }
    const web3 = new Web3(new IpcProvider(IPC_PATH));
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
    const states = POLICY.map(p => p.state);
    const actions = POLICY.map(p => p.action);
    try {
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
