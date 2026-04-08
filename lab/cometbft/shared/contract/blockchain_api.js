/* Server API per la comunicazione tra nodi blockchain e IDS. */

const http = require('http');
const fs = require('fs');
const { Web3 } = require('web3');

// AGENTE HTTP KEEP-ALIVE
const keepAliveAgent = new http.Agent({
    keepAlive: true,
    maxSockets: 512,
    timeout: 60000
});

const PORT = 3000;
const RPC_URL = 'http://localhost:8545';
const ADDR_FILE = '/home/cometbft/data/contract_address.txt';
if (!fs.existsSync(ADDR_FILE)) {
} else {
    var DATA_ROOT = '/home/cometbft/data';
}
const ADDR_PATH = `${DATA_ROOT}/contract_address.txt`;
const ABI_PATH = `${DATA_ROOT}/contract_abi.json`;

let web3, contract, account;
let currentAlertId = 0;
let txNonce = null;
let totalAlertsProcessed = 0;
let totalAlertsReceived = 0;

const VALID_ALERTS = [
    'SAFE_ENVIRONMENT', 'SQL_INJECTION', 'XSS_ATTACK', 'PATH_TRAVERSAL', 'COMMAND_INJECTION'
];

let lastVotedState = {};
let batchBuffer = {};
let lastProcessedBlock = 0;
let lastAlertTime = 0;

let txQueue = [];
let activeTxCount = 0;
const MAX_CONCURRENT_TX = 512;
const MAX_RETRIES = 50;
let isSyncing = false;

async function waitFor(path, timeout = 60000) {
    const t = Date.now();
    while (!fs.existsSync(path)) {
        if (Date.now() - t > timeout) throw new Error(`Timeout waiting for file: ${path}`);
        await new Promise(r => setTimeout(r, 1000));
    }
}

async function waitForRpc(timeout = 60000) {
    const t = Date.now();
    while (true) {
        try {
            await web3.eth.getBlockNumber();
            return;
        } catch (_) { }
        if (Date.now() - t > timeout) throw new Error('RPC timeout');
        await new Promise(r => setTimeout(r, 1000));
    }
}

async function init() {
    web3 = new Web3(new Web3.providers.HttpProvider(RPC_URL, {
        keepAlive: true,
        agent: { http: keepAliveAgent }
    }));
    await waitForRpc();

    let pk = process.argv[2];
    if (pk) {
        pk = pk.trim().replace(/[^a-fA-F0-9]/g, ''); // Sanitizzazione chiave per sicurezza
        if (!pk.startsWith('0x')) pk = '0x' + pk;
        const walletAccount = web3.eth.accounts.privateKeyToAccount(pk);
        web3.eth.accounts.wallet.add(walletAccount);
        account = walletAccount.address;
        console.log(`[INIT] Using local account (private key): ${account}`);
    } else {
        const accounts = await web3.eth.getAccounts();
        if (accounts.length === 0) throw new Error("FATAL: No accounts found.");
        account = accounts[0];
        console.log(`[INIT] Using remote account (unlocked): ${account}`);
    }

    txNonce = await Number(await web3.eth.getTransactionCount(account));
    lastProcessedBlock = await Number(await web3.eth.getBlockNumber());

    await waitFor(ADDR_PATH);
    await waitFor(ABI_PATH);

    const abi = JSON.parse(fs.readFileSync(ABI_PATH, 'utf8'));
    const addr = fs.readFileSync(ADDR_PATH, 'utf8').trim();
    contract = new web3.eth.Contract(abi, addr);

    console.log(`[INIT] API Ready. Contract: ${addr}`);
    pollBlocks();
}

async function pollBlocks() {
    while (true) {
        try {
            const currentBlock = await Number(await web3.eth.getBlockNumber());
            const hasPending = Object.keys(batchBuffer).length > 0;
            const idleTime = Date.now() - lastAlertTime;

            if (hasPending && (currentBlock > lastProcessedBlock || (lastAlertTime > 0 && idleTime > 200))) {
                processBatch().catch(() => { });
            }
            if (currentBlock > lastProcessedBlock) {
                lastVotedState = {};
                lastProcessedBlock = currentBlock;
            }
        } catch (e) { }
        await new Promise(r => setTimeout(r, 500));
    }
}

function enqueueAlert(data) {
    if (!contract || !account) throw new Error('Not initialized');
    totalAlertsReceived++;
    const type = (data.type || '').toUpperCase().replace(/ /g, '_');
    if (!VALID_ALERTS.includes(type)) throw new Error(`Unknown alert type: ${type}`);
    const value = data.value !== undefined ? data.value : 1;

    if (lastVotedState[type] === value) return { success: true, status: 'deduplicated', type, value };

    batchBuffer[type] = value;
    lastAlertTime = Date.now();
    currentAlertId++;
    return { success: true, status: 'batched', alertId: currentAlertId, type, value };
}

async function stressAlert(data) {
    if (!contract || !account) throw new Error('Not initialized');
    totalAlertsReceived++;
    const type = (data.type || 'SQL_INJECTION').toUpperCase().replace(/ /g, '_');
    if (!VALID_ALERTS.includes(type)) throw new Error(`Unknown alert type: ${type}`);

    const value = data.value !== undefined ? data.value : 1;
    lastAlertTime = Date.now();
    currentAlertId++;

    sendToBlockchain([type], [value]).then(() => {
        totalAlertsProcessed++;
    }).catch(async (e) => {
        if (!isSyncing) {
            isSyncing = true;
            try { txNonce = await Number(await web3.eth.getTransactionCount(account)); }
            catch (e2) { } finally { isSyncing = false; }
        }
    });

    return { success: true, status: 'Stress alert queued for blockchain', alertId: currentAlertId, type, value };
}

async function processBatch() {
    let params = Object.keys(batchBuffer);
    if (params.length === 0) return;

    let snapshot = { ...batchBuffer };
    batchBuffer = {};
    let finalValues = params.map(p => snapshot[p]);

    try {
        sendToBlockchain(params, finalValues).catch(() => { });
        Object.assign(lastVotedState, snapshot);
        totalAlertsProcessed += params.length;
    } catch (error) {
        Object.assign(batchBuffer, snapshot);
        txNonce = await Number(await web3.eth.getTransactionCount(account));
    }
}

async function processTxQueue() {
    if (activeTxCount >= MAX_CONCURRENT_TX) return;

    while (txQueue.length > 0 && activeTxCount < MAX_CONCURRENT_TX) {
        const req = txQueue.shift();
        const currentNonce = txNonce++;
        activeTxCount++;

        // FUNZIONE UNIFICATA DI RETRY (Salva gli alert da qualsiasi tipo di errore)
        const handleRetry = async (err) => {
            activeTxCount--;

            // Risincronizza il nonce leggendo il bordo della mempool ('pending')
            try {
                const pendingNonce = await Number(await web3.eth.getTransactionCount(account, 'pending'));
                txNonce = pendingNonce; // Evita i buchi di nonce
            } catch (e) { }

            if (req.retryCount < MAX_RETRIES) {
                req.retryCount++;
                // Aspetta 500ms per far respirare la chain, poi riprova
                setTimeout(() => {
                    txQueue.push(req); // Mettiamo in FONDO alla coda, non in testa!
                    processTxQueue();
                }, 500);
            } else {
                console.error(`[TX] Drop definitivo dopo ${MAX_RETRIES} retry. Errore:`, err.message);
                req.reject(err);
                process.nextTick(processTxQueue);
            }
        };

        try {
            const txPromise = contract.methods.proposeNewValues(req.params, req.values).send({
                from: account,
                gas: 1000000,
                gasPrice: '0',
                type: '0x0',
                nonce: currentNonce
            });

            txPromise
                .on('transactionHash', (hash) => {
                    activeTxCount--;
                    req.resolve(hash);
                    process.nextTick(processTxQueue);
                })
                .on('error', handleRetry) // Cattura errori asincroni (es. mempool piena)
                .catch(() => { });

        } catch (syncError) {
            // CATTURA ERRORI SINCRONI (es. Disconnessione temporanea RPC) E RIPROVA!
            handleRetry(syncError);
        }
    }
}

function sendToBlockchain(params, values) {
    return new Promise((resolve, reject) => {
        // Circuit Breaker alzato a 10.000 (assorbe l'intero stress test N=5000 su un singolo nodo)
        if (txQueue.length > 10000) return reject(new Error('Tx Queue Overflow'));
        txQueue.push({ params, values, resolve, reject, retryCount: 0 });
        processTxQueue().catch(() => { });
    });
}
const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'POST' && req.url === '/alert') {
        let body = ''; req.on('data', c => body += c);
        req.on('end', () => {
            try { res.writeHead(202); res.end(JSON.stringify(enqueueAlert(JSON.parse(body)))); }
            catch (e) { res.writeHead(400); res.end(JSON.stringify({ error: e.message })); }
        });
    } else if (req.method === 'GET' && req.url === '/stats') {
        res.writeHead(200); res.end(JSON.stringify({ totalAlertsProcessed, totalAlertsReceived }));
    } else if (req.method === 'GET' && req.url === '/alive') {
        res.writeHead(200); res.end(JSON.stringify({ status: "ok", account: account }));
    } else if (req.method === 'POST' && req.url === '/stress') {
        let body = ''; req.on('data', c => body += c);
        req.on('end', async () => {
            try { res.writeHead(202); res.end(JSON.stringify(await stressAlert(body ? JSON.parse(body) : {}))); }
            catch (e) { res.writeHead(400); res.end(JSON.stringify({ error: e.message })); }
        });
    } else {
        res.writeHead(404); res.end();
    }
});

(async () => {
    try {
        await init();
        server.listen(PORT, '0.0.0.0', () => console.log(`API on :${PORT} Ready`));
    } catch (e) {
        console.error(e);
        process.exit(1);
    }
})();