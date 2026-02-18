const path = require('path');
const fs = require('fs');
const { Web3 } = require('web3');
const { IpcProvider } = require('web3-providers-ipc');

var web3 = new Web3(new IpcProvider('/home/qbft/data/geth.ipc'));
//const web3 = new Web3(new Web3.providers.WebsocketProvider('ws://localhost:32000'));

const [, , contractABIJson, deployedAddress] = process.argv;
const abi = JSON.parse(contractABIJson);

const myContract = new web3.eth.Contract(abi, deployedAddress);
myContract.handleRevert = true;

console.log('Indirizzo del contratto:', myContract.options.address);
console.log('Eventi disponibili:', Object.keys(myContract.events));


const listenToEvents = async () => {
	console.log('In attesa di eventi...');
	/*
	const providersAccounts = await web3.eth.getAccounts();
	const defaultAccount = providersAccounts[0];
	const e = myContract.events.ActionRequired({
		filter: {
			selectedAgent: defaultAccount
		},
		});
		*/
	const e = myContract.events.ActionRequired();
	e.on('data', (event) => {
		console.log('#end Time: ' + new Date().getTime())
		console.log('Evento ricevuto:', event);
		//fs.writeFileSync('/home/qbft/LOG.txt', event)
		//web3.provider.disconnect();
	});
	e.on('error', (error) => {
		console.error('Errore nel ricevere evento:', error);
	});
};

// Avvio dello script
listenToEvents();



