const path = require('path');
const fs = require('fs');
const { Web3 } = require('web3');
const { IpcProvider } = require('web3-providers-ipc');

var web3 = new Web3(new IpcProvider('/home/qbft/data/geth.ipc'));

const [, , contractABIJson, deployedAddress] = process.argv;
const abi = JSON.parse(contractABIJson);

const myContract = new web3.eth.Contract(abi, deployedAddress);
myContract.handleRevert = true;

console.log('Indirizzo del contratto:', myContract.options.address);
console.log('Eventi disponibili:', Object.keys(myContract.events));

// Poll for ActionRequired events instead of using real-time subscriptions,
// which silently fail with Web3.js v4 over IPC.
const POLL_INTERVAL_MS = 2000;

const pollForEvents = async () => {
	let lastBlock = BigInt(await web3.eth.getBlockNumber());

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
				const action = event.returnValues.actionToApply;
				const agent = event.returnValues.selectedAgent;
				const logMsg = `[${new Date().toISOString()}] ACTUATOR: Executing ${action} on agent ${agent}\n`;

				console.log(logMsg);
				fs.appendFileSync('/var/log/actuator.log', logMsg);
			}

			lastBlock = currentBlock;
		} catch (err) {
			console.error(`[${new Date().toISOString()}] Polling error: ${err.message}`);
		}
	}, POLL_INTERVAL_MS);
};

// Avvio dello script
pollForEvents();
