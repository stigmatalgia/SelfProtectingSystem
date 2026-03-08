const path = require('path');
const fs = require('fs');
const http = require('http');
const { Web3 } = require('web3');
const { IpcProvider } = require('web3-providers-ipc');

const web3 = new Web3(new IpcProvider('/home/qbft/data/geth.ipc'));

let [, , contractABIJson, deployedAddress, actuatorUrl] = process.argv;


if (!contractABIJson || contractABIJson.trim() === '') {
    contractABIJson = fs.readFileSync('/home/qbft/data/contract_abi.json', 'utf8');
}

if (!deployedAddress || deployedAddress.trim() === '') {
    deployedAddress = fs.readFileSync('/home/qbft/data/contract_address.txt', 'utf8').trim();
}

if (!actuatorUrl || actuatorUrl.trim() === '') {
    actuatorUrl = 'http://172.16.4.1:5000/action';
}

const abi = JSON.parse(contractABIJson);

const myContract = new web3.eth.Contract(abi, deployedAddress);
myContract.handleRevert = true;

console.log('Contract address:', myContract.options.address);
console.log('Actuator URL:', actuatorUrl);
console.log('Available events:', Object.keys(myContract.events));

function forwardToActuator(action, agent) {
    const url = new URL(actuatorUrl);
    const payload = JSON.stringify({
        action: action,
        selectedAgent: agent,
        timestamp: new Date().toISOString(),
        source: 'member3'
    });

    const options = {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(payload)
        },
        timeout: 5000
    };

    const req = http.request(options, (res) => {
        let body = '';
        res.on('data', (chunk) => body += chunk);
        res.on('end', () => {
            console.log(`[${new Date().toISOString()}] Actuator response: ${res.statusCode} ${body}`);
        });
    });

    req.on('error', (err) => {
        console.error(`[${new Date().toISOString()}] Failed to forward to actuator: ${err.message}`);
    });

    req.on('timeout', () => {
        console.error(`[${new Date().toISOString()}] Actuator request timed out`);
        req.destroy();
    });

    req.write(payload);
    req.end();
}

const POLL_INTERVAL_MS = 2000;

const pollForEvents = async () => {
    let lastBlock = 0n;
    console.log(`Starting event polling from block ${lastBlock}...`);

    setInterval(async () => {
        try {
            const currentBlock = BigInt(await web3.eth.getBlockNumber());
            if (currentBlock <= lastBlock) return;

            const fromBlock = lastBlock + 1n;
            const events = await myContract.getPastEvents('ActionRequired', {
                fromBlock: fromBlock.toString(),
                toBlock: currentBlock.toString()
            });

            for (const event of events) {
                console.log('#end Time: ' + new Date().getTime());

                console.log('Raw returnValues:', event.returnValues);
                const agent = event.returnValues['0'];
                const action = event.returnValues['1'];

                const logMsg = `[${new Date().toISOString()}] ACTUATOR_FORWARDER: Action=${action} Agent=${agent}\n`;

                console.log(logMsg);
                fs.appendFileSync('/var/log/actuator_forwarder.log', logMsg);

                forwardToActuator(action, agent);
            }

            lastBlock = currentBlock;
        } catch (err) {
            console.error(`[${new Date().toISOString()}] Polling error: ${err.message}`);
        }
    }, POLL_INTERVAL_MS);
};

pollForEvents();