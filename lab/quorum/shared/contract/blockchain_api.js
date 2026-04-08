/* Server che i nodi membri della blockchain usano per la comunicazione con gli IDS 
in ricezione + trasmutazione da alert a transazione in blockchain proposta ai validatori
*/

const http = require('http');
const fs = require('fs');
const { Web3 } = require('web3');
const { IpcProvider } = require('web3-providers-ipc');

const PORT = 3000;
const IPC_PATH = '/home/qbft/data/geth.ipc';
const ADDR_PATH = '/home/qbft/data/contract_address.txt';
const ABI_PATH = '/home/qbft/data/contract_abi.json';

let web3, contract, account;
let currentAlertId = 0;
let txNonce = null;
let txQueue = [];
let isSending = false;
let activeTxCount = 0;
const MAX_CONCURRENT_TX = 32;
const MAX_RETRIES = 5;       // Maximum retry attempts for failed transactions
let totalAlertsProcessed = 0;
let totalAlertsReceived = 0;

const VALID_ALERTS = [
    'SAFE_ENVIRONMENT',
    'SQL_INJECTION',
    'XSS_ATTACK',
    'PATH_TRAVERSAL',
    'COMMAND_INJECTION'
];

// --- STATE & BATCHING CONFIG ---
let lastVotedState = {}; // cache: { PARAM_NAME: value }
let batchBuffer = {};    // pending: { PARAM_NAME: value }
let lastProcessedBlock = 0;
let lastAlertTime = 0;

async function waitFor(path, timeout = 60000) {
    console.log(`[INIT] Waiting for ${path}...`);
    const t = Date.now();
    while (!fs.existsSync(path)) {
        if (Date.now() - t > timeout) throw new Error(`Timeout waiting for file: ${path}`);
        await new Promise(r => setTimeout(r, 1000));
    }
}

async function init() {
    await waitFor(IPC_PATH);
    web3 = new Web3(new IpcProvider(IPC_PATH));

    // Attesa che geth sia pronto
    while (true) {
        try {
            const accounts = await web3.eth.getAccounts();
            if (accounts.length > 0) {
                account = accounts[0];
                break;
            }
        } catch (e) { }
        await new Promise(r => setTimeout(r, 1000));
    }

    txNonce = await Number(await web3.eth.getTransactionCount(account));
    lastProcessedBlock = await Number(await web3.eth.getBlockNumber());

    await waitFor(ADDR_PATH);
    await waitFor(ABI_PATH);

    const abi = JSON.parse(fs.readFileSync(ABI_PATH, 'utf8'));
    const addr = fs.readFileSync(ADDR_PATH, 'utf8').trim();
    contract = new web3.eth.Contract(abi, addr);

    console.log(`[INIT] Quorum API Ready. Contract: ${addr} Account: ${account}`);
    pollBlocks();
}

async function pollBlocks() {
    while (true) {
        try {
            const currentBlock = await Number(await web3.eth.getBlockNumber());
            const hasPending = Object.keys(batchBuffer).length > 0;
            const idleTime = Date.now() - lastAlertTime;

            let blockChanged = false;
            if (hasPending && (currentBlock > lastProcessedBlock || (lastAlertTime > 0 && idleTime > 200))) {
                console.log(`[POLL] Triggering batch (Block: ${currentBlock}, Idle: ${idleTime}ms)`);
                processBatch(); // Non-blocking call

                if (currentBlock > lastProcessedBlock) {
                    blockChanged = true;
                }
                lastProcessedBlock = currentBlock;
            } else if (currentBlock > lastProcessedBlock) {
                blockChanged = true;
                lastProcessedBlock = currentBlock;
            }

            if (blockChanged) {
                console.log(`[STATE] Block ${currentBlock} confirmed.`);
                lastVotedState = {};
            }

        } catch (e) {
            console.error("[POLL] Error:", e.message);
        }
        await new Promise(r => setTimeout(r, 500));
    }
}

function enqueueAlert(data) {
    if (!contract || !account) throw new Error('Not initialized');

    totalAlertsReceived++;
    const type = (data.type || '').toUpperCase().replace(/ /g, '_');
    if (!VALID_ALERTS.includes(type)) throw new Error(`Unknown alert type: ${type}`);

    const value = data.value !== undefined ? data.value : 1;

    if (lastVotedState[type] === value) {
        return { success: true, status: 'deduplicated', type, value };
    }

    batchBuffer[type] = value;
    lastAlertTime = Date.now();
    currentAlertId++;

    return { success: true, status: 'batched', alertId: currentAlertId, type, value, stressMode };
}

async function stressAlert(data) {
    if (!contract || !account) throw new Error('Not initialized');

    totalAlertsReceived++;
    const type = (data.type || 'SQL_INJECTION').toUpperCase().replace(/ /g, '_');
    if (!VALID_ALERTS.includes(type)) throw new Error(`Unknown alert type: ${type}`);

    const value = data.value !== undefined ? data.value : 1;
    lastAlertTime = Date.now();
    currentAlertId++;

    // Fire and forget (don't await receipt) to allow high throughput
    sendToBlockchain([type], [value]).then(() => {
        totalAlertsProcessed++;
    }).catch(async (e) => {
        console.error(`[STRESS] Tx failed for nonce ${txNonce - 1}:`, e.message);
        // On failure, re-sync nonce from blockchain to avoid gaps
        txNonce = await Number(await web3.eth.getTransactionCount(account));
    });

    return { success: true, status: 'Stress alert queued for blockchain', alertId: currentAlertId, type, value };
}

async function processBatch() {
    let params = Object.keys(batchBuffer);
    if (params.length === 0) return;

    let snapshot = { ...batchBuffer };
    batchBuffer = {};

    let finalParams = params;
    let finalValues;

    if (snapshot['SAFE_ENVIRONMENT']) {
        finalParams = VALID_ALERTS.filter(a => a !== 'SAFE_ENVIRONMENT');
        finalValues = finalParams.map(() => 0);
    } else {
        finalValues = finalParams.map(p => snapshot[p]);
    }

    try {
        sendToBlockchain(finalParams, finalValues).catch(e => {
            console.error(`[TX] Async failure:`, e.message);
        });

        if (snapshot['SAFE_ENVIRONMENT']) {
            VALID_ALERTS.forEach(a => lastVotedState[a] = 0);
        } else {
            Object.assign(lastVotedState, snapshot);
        }
        totalAlertsProcessed += finalParams.length;
    } catch (error) {
        console.error(`[BATCH] Failed:`, error.message);
        Object.assign(batchBuffer, snapshot);
    }
}

async function processTxQueue() {
    if (activeTxCount >= MAX_CONCURRENT_TX) return;

    while (txQueue.length > 0 && activeTxCount < MAX_CONCURRENT_TX) {
        const req = txQueue.shift();
        const currentNonce = txNonce++;
        activeTxCount++;

        console.log(`[TX] Sending (${req.params.join(',')}) with nonce ${currentNonce}... (Queue: ${txQueue.length}, Active: ${activeTxCount})`);

        contract.methods.proposeNewValues(req.params, req.values).send({
            from: account,
            gas: 1000000,
            gasPrice: '0',
            type: '0x0',
            nonce: currentNonce
        })
            .on('transactionHash', (hash) => {
                console.log(`[TX] Hash: ${hash} (nonce: ${currentNonce})`);
                activeTxCount--;
                req.resolve(hash);
                process.nextTick(processTxQueue);
            })
            .on('error', async (err) => {
                console.error(`[TX] Error (nonce: ${currentNonce}):`, err.message);
                activeTxCount--;

                try {
                    const chainNonce = Number(await web3.eth.getTransactionCount(account));
                    if (chainNonce !== txNonce) {
                        console.log(`[TX] Nonce disallineato (Locale: ${txNonce}, Chain: ${chainNonce}). Risincronizzo...`);
                        txNonce = chainNonce;
                    }
                } catch (e) { }

                if (req.retryCount < MAX_RETRIES) {
                    req.retryCount++;
                    console.warn(`[TX] Retry ${req.retryCount}/${MAX_RETRIES} for transaction (nonce was: ${currentNonce}) in 500ms...`);
                    setTimeout(() => {
                        txQueue.unshift(req); // Prioritize retries
                        processTxQueue();
                    }, 500);
                } else {
                    console.error(`[TX] Max tries reached (${MAX_RETRIES}). Transaction dropped.`);
                    req.reject(err);
                    process.nextTick(processTxQueue);
                }
            });
    }
}

function sendToBlockchain(params, values) {
    return new Promise((resolve, reject) => {
        txQueue.push({ params, values, resolve, reject, retryCount: 0 });
        processTxQueue();
    });
}

const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'POST' && req.url === '/alert') {
        let body = '';
        req.on('data', c => body += c);
        req.on('end', () => {
            try {
                const result = enqueueAlert(JSON.parse(body));
                res.writeHead(202);
                res.end(JSON.stringify(result));
            } catch (e) {
                res.writeHead(400);
                res.end(JSON.stringify({ error: e.message }));
            }
        });
    } else if (req.method === 'GET' && req.url === '/stats') {
        res.writeHead(200);
        res.end(JSON.stringify({ totalAlertsProcessed, totalAlertsReceived }));
    }
    else if (req.method === 'GET' && req.url === '/alive') {
        res.writeHead(200);
        res.end(JSON.stringify({ status: "ok", account: account }));
    }
    else if (req.method === 'POST' && req.url === '/stress') {
        let body = '';
        req.on('data', c => body += c);
        req.on('end', async () => {
            try {
                const data = body ? JSON.parse(body) : {};
                const result = await stressAlert(data);
                res.writeHead(202);
                res.end(JSON.stringify(result));
            } catch (e) {
                res.writeHead(400);
                res.end(JSON.stringify({ error: e.message }));
            }
        });
    }
    else {
        res.writeHead(404);
        res.end();
    }
});

(async () => {
    try {
        await init();
        server.listen(PORT, '0.0.0.0', () => console.log(`Quorum API on :${PORT} Ready`));
    } catch (e) {
        console.error(e);
        process.exit(1);
    }
})();