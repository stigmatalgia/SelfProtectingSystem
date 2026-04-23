/**
 * quorum_native_bench.js — Native throughput benchmark for the Quorum SPS lab.
 *
 * Sends N transactions directly through JSON-RPC (eth_sendTransaction) to member0-2
 * to mirror CometBFT's direct chain-injection methodology as closely as possible.
 */

'use strict';

const http = require('http');
const fs = require('fs');
const { Web3 } = require('web3');

const ADDR_PATH = '/home/qbft/data/contract_address.txt';
const ABI_PATH = '/home/qbft/data/contract_abi.json';

// IPs of member0-2 that run blockchain_api.js on port 3000.
// member3 is on the same blockchain network segment (10.99.0.x).
const MEMBER_IPS = ['10.99.0.11', '10.99.0.12', '10.99.0.13'];
const RPC_PORT = 8545;

const KEEP_ALIVE_AGENT = new http.Agent({
    keepAlive: true,
    maxSockets: 512,
    maxFreeSockets: 64,
});

// Initialize Web3 and Contract (for encoding purposes)
let contract;
try {
    const abi = JSON.parse(fs.readFileSync(ABI_PATH, 'utf8'));
    const addr = fs.readFileSync(ADDR_PATH, 'utf8').trim();
    const web3 = new Web3();
    contract = new web3.eth.Contract(abi, addr);
    process.stderr.write(`[quorum-bench] Initialized contract at ${addr}\n`);
} catch (e) {
    process.stderr.write(`[quorum-bench] ERROR initializing contract: ${e.message}\n`);
}

// ── helpers ────────────────────────────────────────────────────────────────

function postStress(ip, alertType) {
    return new Promise((resolve) => {
        const body = JSON.stringify({ type: alertType, value: 1 });
        const options = {
            hostname: ip,
            port: API_PORT,
            path: '/stress',
            method: 'POST',
            agent: KEEP_ALIVE_AGENT,
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(body),
                'Connection': 'keep-alive',
            },
            timeout: 15000,
        };
        const req = http.request(options, (res) => {
            res.resume();
            res.on('end', () => resolve(res.statusCode < 400));
        });
        req.on('error', () => resolve(false));
        req.on('timeout', () => { req.destroy(); resolve(false); });
        req.write(body);
        req.end();
    });
}

function rpcCall(ip, method, params) {
    return new Promise((resolve) => {
        const payload = JSON.stringify({ jsonrpc: '2.0', method, params, id: 1 });
        const options = {
            hostname: ip,
            port: RPC_PORT,
            path: '/',
            method: 'POST',
            agent: KEEP_ALIVE_AGENT,
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(payload),
                'Connection': 'keep-alive',
            },
            timeout: 15000,
        };

        const req = http.request(options, (res) => {
            let data = '';
            res.on('data', (c) => (data += c));
            res.on('end', () => {
                try {
                    const parsed = JSON.parse(data);
                    resolve(parsed.result);
                } catch {
                    resolve(null);
                }
            });
        });

        req.on('error', () => resolve(null));
        req.on('timeout', () => {
            req.destroy();
            resolve(null);
        });
        req.write(payload);
        req.end();
    });
}

async function sendRpcTx(ip, fromAccount, nonce) {
    if (!fromAccount || !contract) return false;

    const data = contract.methods.proposeNewValues(["SQL_INJECTION"], [1]).encodeABI();

    const tx = {
        from: fromAccount,
        to: contract.options.address,
        data: data,
        value: '0x0',
        gas: '0x30D40',   // Reduced to 200,000 gas (sufficient for proposeNewValues)
        gasPrice: '0x0',
        nonce: '0x' + nonce.toString(16),
    };

    // Fire and forget: we don't await rpcCall result
    rpcCall(ip, 'eth_sendTransaction', [tx]).catch(() => { });
    return true;
}

async function getNodePrimaryAccount(ip) {
    const accounts = await rpcCall(ip, 'eth_accounts', []);
    if (Array.isArray(accounts) && accounts.length > 0) {
        return accounts[0];
    }
    return null;
}

async function getNodeCommittedTx(ip, account) {
    if (!account) return 0;
    const latestHex = await rpcCall(ip, 'eth_getTransactionCount', [account, 'latest']);
    if (typeof latestHex !== 'string') return 0;
    try {
        return parseInt(latestHex, 16) || 0;
    } catch {
        return 0;
    }
}

async function getTotalCommitted(accountsByIp) {
    let total = 0;
    for (const ip of MEMBER_IPS) {
        total += await getNodeCommittedTx(ip, accountsByIp[ip]);
    }
    return total;
}

async function sendWithRetry(ip, fromAccount) {
    return sendRpcTx(ip, fromAccount);
}

// ── main ───────────────────────────────────────────────────────────────────

async function main() {
    const n = parseInt(process.argv[2] || '0', 10);
    if (!n || n <= 0) {
        process.stderr.write('Usage: node quorum_native_bench.js <N>\n');
        process.exit(1);
    }

    process.stderr.write(`[quorum-bench] N=${n} targets=${MEMBER_IPS.join(',')}\n`);

    const accountsByIp = {};
    const noncesByIp = {};
    for (const ip of MEMBER_IPS) {
        accountsByIp[ip] = await getNodePrimaryAccount(ip);
        if (accountsByIp[ip]) {
            noncesByIp[ip] = await getNodeCommittedTx(ip, accountsByIp[ip]);
        } else {
            process.stderr.write(`[quorum-bench] WARN: could not resolve account for ${ip}\n`);
            noncesByIp[ip] = 0;
        }
    }

    const baselineCommitted = await getTotalCommitted(accountsByIp);
    process.stderr.write(`[quorum-bench] Baseline committedTx=${baselineCommitted}\n`);

    const t0 = Date.now();
    let sent = 0;
    let errors = 0;

    const envConcurrency = parseInt(process.env.QUORUM_BENCH_CONCURRENCY || '', 10);
    const desiredConcurrency = Number.isFinite(envConcurrency) && envConcurrency > 0
        ? envConcurrency
        : Math.min(n, 200);
    const CONCURRENCY = Math.min(desiredConcurrency, n);

    const tasks = Array.from({ length: n }, (_, i) => {
        const ip = MEMBER_IPS[i % MEMBER_IPS.length];
        const account = accountsByIp[ip];
        const nonce = noncesByIp[ip]++;
        return async () => {
            const ok = await sendRpcTx(ip, account, nonce);
            if (ok) {
                sent++;
                if (sent % 100 === 0 || sent === n) {
                    process.stdout.write(`[quorum-bench] Progress: ${sent}/${n} dispatched...\n`);
                }
            } else {
                errors++;
            }
        };
    });

    async function runPool(tasks, limit) {
        let idx = 0;
        const workers = Array.from({ length: limit }, async () => {
            while (idx < tasks.length) {
                const task = tasks[idx++];
                await task();
            }
        });
        await Promise.all(workers);
    }

    await runPool(tasks, CONCURRENCY);
    const sendDoneMs = Date.now() - t0;
    const sentTimeSec = sendDoneMs / 1000;

    process.stderr.write(`[quorum-bench] Dispatches done in ${sentTimeSec.toFixed(3)}s. Polling committed tx…\n`);

    const adaptiveDeadlineMs = Math.max(60000, (n / 500) * 1000);
    const deadline = Date.now() + adaptiveDeadlineMs;
    const settleWindow = 12000;
    const minObserve = 8000;
    const pollStart = Date.now();

    let best = await getTotalCommitted(accountsByIp);
    let lastProgress = Date.now();
    let sawProgress = false;

    while (true) {
        await new Promise((r) => setTimeout(r, 1000));
        const current = await getTotalCommitted(accountsByIp);
        const committed = current - baselineCommitted;

        if (current > best) {
            best = current;
            sawProgress = true;
            lastProgress = Date.now();
        }

        process.stderr.write(`[quorum-bench] committed=${committed}/${n}\n`);

        if (committed >= n) break;
        const now = Date.now();
        if (sawProgress && (now - pollStart) >= minObserve && (now - lastProgress) >= settleWindow) break;
        if (now >= deadline) break;
    }

    const wallSec = (Date.now() - t0) / 1000;
    const finalCommitted = Math.max(0, best - baselineCommitted);

    const stats = {
        N: n,
        Sent: sent,
        SentTime: sentTimeSec,
        Transactions: finalCommitted,
        SuccessRate: n > 0 ? (finalCommitted / n) * 100 : 100,
        TotalTimeSeconds: wallSec,
        TPS: wallSec > 0 ? finalCommitted / wallSec : 0
    };

    process.stdout.write('BENCH_STATS:' + JSON.stringify(stats) + '\n');
    KEEP_ALIVE_AGENT.destroy();
}

main().catch((e) => {
    process.stderr.write('[quorum-bench] FATAL: ' + e.message + '\n');
    process.exit(1);
});
