/* 
Server che i nodi membri della blockchain usano per la comunicazione con gli IDS 
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

// POSSIBILI TIPI DI STATO + safe_environment che non entra come nuova variabile nello stato
// ma e' rappresentato dallo stato 0000
const VALID_ALERTS = [
    'SAFE_ENVIRONMENT',
    'SQL_INJECTION',
    'XSS_ATTACK',
    'PATH_TRAVERSAL',
    'BRUTE_FORCE'
];

// Helper per aspettare la creazione di file da altri processi
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

    await waitFor(ADDR_FILE);
    await waitFor(ABI_FILE);

    let abi;
    // Entro 20 secondi deve essere pronto o considero fallito
    for (let i = 0; i < 10; i++) {
        try {
            const raw = fs.readFileSync(ABI_FILE, 'utf8');
            if (raw.length > 0) { abi = JSON.parse(raw); break; }
        } catch (_) { }
        await new Promise(r => setTimeout(r, 500));
    }
    if (!abi) throw new Error('Failed to load ABI');

    contract = new web3.eth.Contract(abi, fs.readFileSync(ADDR_FILE, 'utf8').trim());
    contract.handleRevert = true;
}

// Se lo stato e' safe imposta il codice di stato a 0000
// altrimenti effettua la conversione mettendo a 1 il bit corrispondente al tipo di stato.
async function submitAlert(data) {
    if (!contract || !account) return { success: false, error: 'Not initialized' };

    const type = (data.type || '').toUpperCase().replace(/ /g, '_');
    if (!VALID_ALERTS.includes(type)) return { success: false, error: `Unknown alert type: ${type}` };

    let params, values;
    if (type === 'SAFE_ENVIRONMENT') {
        params = VALID_ALERTS.filter(a => a !== 'SAFE_ENVIRONMENT');
        values = params.map(() => 0);
    } else {
        params = [type];
        values = [1];
    }

    // Invia la transazione alla blockchain
    const tx = await contract.methods.proposeNewValues(params, values).send({
        from: account, gas: 1600000, gasPrice: '0', type: '0x0'
    });

    return { success: true, tx: tx.transactionHash, type };
}

// Server HTTP per ricevere gli alert con 3 endpoint:
// - POST /alert: invia un alert alla blockchain
// - GET /alive: verifica se il server risponde (Per i test)
// - GET /status: restituisce lo stato del server
const server = http.createServer(async (req, res) => {
    res.setHeader('Content-Type', 'application/json');

    if (req.method === 'POST' && req.url === '/alert') {
        let body = '';
        req.on('data', c => body += c);
        req.on('end', async () => {
            try {
                const result = await submitAlert(JSON.parse(body));
                res.writeHead(result.success ? 200 : 500);
                res.end(JSON.stringify(result));
            } catch (e) {
                res.writeHead(400);
                res.end(JSON.stringify({ error: 'Invalid JSON' }));
            }
        });
        // Boilerplate per il testing mid-development
    } else if (req.method === 'GET' && req.url === '/alive') {
        res.writeHead(200);
        res.end(JSON.stringify({ status: 'ok', account }));
    } else if (req.method === 'GET' && req.url === '/status') {
        try {
            res.writeHead(200);
            res.end(JSON.stringify({ status: 'ok', block: (await web3.eth.getBlockNumber()).toString() }));
        } catch (e) {
            res.writeHead(500);
            res.end(JSON.stringify({ error: e.message }));
        }
    } else {
        res.writeHead(404);
        res.end(JSON.stringify({ error: 'Not found' }));
    }
});

(async () => {
    try {
        await init();
        server.listen(PORT, '0.0.0.0', () => console.log(`API on :${PORT} account=${account}`));
    } catch (e) {
        console.error(e);
        process.exit(1);
    }
})();
