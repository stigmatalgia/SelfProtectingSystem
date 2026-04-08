const http = require('http');

const n = parseInt(process.argv[2]);
const ips = process.argv.slice(3);

if (isNaN(n) || ips.length === 0) {
    console.log("Usage: node blockchain_burst.js <N> [node_ips...]");
    process.exit(1);
}

console.log(`--- Blockchain Direct Burst START (N=${n}) ---`);
console.log(`Targets: ${ips.join(', ')}`);

const start = Date.now();
let completed = 0;
let errors = 0;

const sendRequest = (ip) => {
    return new Promise((resolve) => {
        const data = JSON.stringify({ type: "SQL_INJECTION", value: 1 });
        const options = {
            hostname: ip,
            port: 3000,
            path: '/stress',
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': data.length
            },
            timeout: 10000
        };

        const req = http.request(options, (res) => {
            res.on('data', () => {});
            res.on('end', () => resolve(true));
        });

        req.on('error', () => resolve(false));
        req.on('timeout', () => {
            req.destroy();
            resolve(false);
        });
        
        req.write(data);
        req.end();
    });
};

async function burst() {
    const promises = [];
    for (let i = 0; i < n; i++) {
        const ip = ips[i % ips.length];
        promises.push(sendRequest(ip).then(success => {
            completed++;
            if (!success) errors++;
        }));
    }
    await Promise.all(promises);
    const duration = (Date.now() - start) / 1000;
    console.log(`--- Blockchain Direct Burst END (Duration: ${duration.toFixed(2)}s, Errors: ${errors}) ---`);
}

burst();
