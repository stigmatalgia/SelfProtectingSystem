const http = require('http');
const fs = require('fs');
const { Web3 } = require('web3');
const { IpcProvider } = require('web3-providers-ipc');

const PORT = 3000;
const IPC_PATH = '/home/qbft/data/geth.ipc';
const ADDR_FILE = '/home/qbft/data/contract_address.txt';
const ABI_FILE = '/home/qbft/data/contract_abi.json';

let web3, contract, account;

const ALERT_TO_PARAM = {
    'SQL_INJECTION': 'P_0_0',
    'XSS_ATTACK': 'P_0_1',
    'PATH_TRAVERSAL': 'P_0_2',
    'BRUTE_FORCE': 'P_0_3'
};

const SEVERITY_TO_VALUE = { 'low': 2, 'medium': 3, 'high': 4 };

async function waitFor(path, timeout = 50000) {
    const t = Date.now();
    while (!fs.existsSync(path)) {
        if (Date.now() - t > timeout) throw new Error(`Timeout: ${path}`);
        await new Promise(r => setTimeout(r, 2000));
    }
}

async function init() {
    await waitFor(IPC_PATH);
    web3 = new Web3(new IpcProvider(IPC_PATH));
    account = (await web3.eth.getAccounts())[0];

    await waitFor(ADDR_FILE);
    await waitFor(ABI_FILE);

    let abi;
    for (let i = 0; i < 10; i++) {
        try {
            const raw = fs.readFileSync(ABI_FILE, 'utf8');
            if (raw.length > 0) { abi = JSON.parse(raw); break; }
        } catch (_) { }
        await new Promise(r => setTimeout(r, 2000));
    }
    if (!abi) throw new Error('Failed to load ABI');

    contract = new web3.eth.Contract(abi, fs.readFileSync(ADDR_FILE, 'utf8').trim());
    contract.handleRevert = true;
}

async function submitAlert(data) {
    if (!contract || !account) return { success: false, error: 'Not initialized' };

    const type = (data.type || '').toUpperCase().replace(/ /g, '_');
    const param = ALERT_TO_PARAM[type];
    if (!param) return { success: false, error: `Unknown alert type: ${type}` };

    const value = SEVERITY_TO_VALUE[(data.severity || 'medium').toLowerCase()] || 3;

    const tx = await contract.methods.proposeNewValues([param], [value]).send({
        from: account, gas: 1600000, gasPrice: 0
    });

    return { success: true, tx: tx.transactionHash, param, value };
}

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
