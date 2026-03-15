/* Server che i nodi membri della blockchain usano per la comunicazione con gli IDS 
in ricezione + trasmutazione da alert a transazione in blockchain proposta ai validatori
*/

const http = require('http');
const fs = require('fs');
const { Web3 } = require('web3');
const { IpcProvider } = require('web3-providers-ipc');

const PORT = 3000;
const IPC_PATH = '/home/qbft/data/geth.ipc';
const ADDR_FILE = '/home/qbft/data/contract_address.txt';
const ABI_FILE = '/home/qbft/data/contract_abi.json';

let web3, contract, account;
let currentAlertId = 0;
let txNonce = null;

const VALID_ALERTS = [
    'SAFE_ENVIRONMENT',
    'SQL_INJECTION',
    'XSS_ATTACK',
    'PATH_TRAVERSAL',
    'COMMAND_INJECTION'
];

// --- GESTIONE CODA ASINCRONA ---
const alertQueue = [];
let isProcessingQueue = false;

async function waitFor(path, timeout = 50000) {
    const t = Date.now();
    while (!fs.existsSync(path)) {
        if (Date.now() - t > timeout) throw new Error(`Timeout: ${path}`);
        await new Promise(r => setTimeout(r, 500));
    }
}

async function init() {
    await waitFor(IPC_PATH);
    web3 = new Web3(new IpcProvider(IPC_PATH));
    account = (await web3.eth.getAccounts())[0];

    txNonce = await Number(await web3.eth.getTransactionCount(account));

    await waitFor(ADDR_FILE);
    await waitFor(ABI_FILE);

    let abi;
    for (let i = 0; i < 10; i++) {
        try {
            const raw = fs.readFileSync(ABI_FILE, 'utf8');
            if (raw.length > 0) { abi = JSON.parse(raw); break; }
        } catch (_) { }
        await new Promise(r => setTimeout(r, 500));
    }
    if (!abi) throw new Error('Failed to load ABI');

    contract = new web3.eth.Contract(abi, fs.readFileSync(ADDR_FILE, 'utf8').trim());
}

function enqueueAlert(data) {
    if (!contract || !account) throw new Error('Not initialized');

    const type = (data.type || '').toUpperCase().replace(/ /g, '_');
    if (!VALID_ALERTS.includes(type)) throw new Error(`Unknown alert type: ${type}`);

    const value = data.value !== undefined ? data.value : 1;
    currentAlertId++;
    const alertId = currentAlertId;

    alertQueue.push({ type, alertId, value });

    processQueue().catch(err => console.error("Errore critico nella coda:", err));

    return { success: true, status: 'queued', alertId, type, value };
}

async function processQueue() {
    if (isProcessingQueue || alertQueue.length === 0) return;
    isProcessingQueue = true;

    while (alertQueue.length > 0) {
        const { type, alertId, value } = alertQueue.shift();

        if (type === 'SAFE_ENVIRONMENT' && alertId < currentAlertId) {
            console.log(`[QUEUE] Salto SAFE_ENVIRONMENT obsoleto (AlertID: ${alertId})`);
            continue;
        }

        try {
            await sendToBlockchain(type, alertId, value);
        } catch (error) {
            console.error(`[QUEUE] Errore invio transazione per ${type}:`, error.message);
            if (account) txNonce = await Number(await web3.eth.getTransactionCount(account));
        }
    }
    isProcessingQueue = false;
}

async function sendToBlockchain(type, alertId, value = 1) {
    let params, values;
    if (type === 'SAFE_ENVIRONMENT') {
        params = VALID_ALERTS.filter(a => a !== 'SAFE_ENVIRONMENT');
        values = params.map(() => 0);
    } else {
        params = [type];
        values = [value];
    }

    const currentNonce = txNonce++;
    console.log(`[${type}] Invio tx alla chain (value=${value}) con nonce ${currentNonce}...`);

    const tx = await contract.methods.proposeNewValues(params, values).send({
        from: account,
        gas: 1600000,
        gasPrice: '0',
        type: '0x0',
        nonce: currentNonce
    });

    console.log(`[${type}] Tx confermata al blocco ${tx.blockNumber}. AlertID: ${alertId}`);
}

// --- SERVER HTTP ---
const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');

    if (req.method === 'POST' && req.url === '/alert') {
        let body = '';
        req.on('data', c => body += c);
        req.on('end', () => {
            let parsedBody;
            try {
                parsedBody = JSON.parse(body);
            } catch (e) {
                res.writeHead(400);
                return res.end(JSON.stringify({ error: 'Invalid JSON' }));
            }

            try {
                const result = enqueueAlert(parsedBody);
                res.writeHead(202);
                res.end(JSON.stringify(result));
            } catch (e) {
                res.writeHead(500);
                res.end(JSON.stringify({ error: e.message }));
            }
        });
    } else if (req.method === 'GET' && req.url === '/alive') {
        res.writeHead(200);
        res.end(JSON.stringify({ status: 'ok', account, pendingQueue: alertQueue.length }));
    } else {
        res.writeHead(404);
        res.end(JSON.stringify({ error: 'Not found' }));
    }
});

(async () => {
    try {
        await init();
        server.listen(PORT, '0.0.0.0', () => console.log(`API on :${PORT} account=${account} - Ready for logs`));
    } catch (e) {
        console.error(e);
        process.exit(1);
    }
})();