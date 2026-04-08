const solc = require('solc');
const path = require('path');
const fs = require('fs');
const { Web3 } = require('web3');

const contractName = 'IDS';
const fileName = `${contractName}.sol`;
const contractPath = path.join(__dirname, fileName);
const sourceCode = fs.readFileSync(contractPath, 'utf8');

const input = {
	language: 'Solidity',
	sources: {
		[fileName]: { content: sourceCode },
	},
	settings: {
		outputSelection: {
			'*': {
				'*': ['abi', 'evm.bytecode'],
			},
		},
	},
};

// Usa __dirname come base per risolvere gli import Solidity.
// perche' solc risolve i percorsi relativi rispetto all'unita' sorgente che importa
// prima di chiamare questa callback.
function findImportsLoc(importPath) {
	try {
		const resolvedPath = path.resolve(__dirname, importPath);
		return { contents: fs.readFileSync(resolvedPath, 'utf8') };
	} catch (e) {
		return { error: `File not found: ${importPath} (resolved to ${path.resolve(__dirname, importPath)})` };
	}
}

let compiledCode;
try {
	compiledCode = JSON.parse(solc.compile(JSON.stringify(input), { import: findImportsLoc }));
} catch (e) {
	console.error('Fatal: Solidity compilation threw an exception:', e);
	process.exit(1);
}
if (compiledCode.errors) {
	const errors = compiledCode.errors.filter(e => e.severity === 'error');
	if (errors.length > 0) {
		console.error('Solidity compilation errors:');
		errors.forEach(e => console.error(e.formattedMessage || e.message));
		process.exit(1);
	}
	const warnings = compiledCode.errors.filter(e => e.severity === 'warning');
	if (warnings.length > 0) {
		console.warn('Solidity compilation warnings:');
		warnings.forEach(w => console.warn(w.formattedMessage || w.message));
	}
}

if (!compiledCode.contracts || !compiledCode.contracts[fileName] || !compiledCode.contracts[fileName][contractName]) {
	console.error('Fatal: Contract compilation produced no output for', contractName);
	console.error('Compiled output:', JSON.stringify(compiledCode, null, 2));
	process.exit(1);
}

const bytecode = compiledCode.contracts[fileName][contractName].evm.bytecode.object;

const abi = compiledCode.contracts[fileName][contractName].abi;

var web3 = new Web3(new Web3.providers.HttpProvider('http://localhost:8545'));

const myContract = new web3.eth.Contract(abi);
myContract.handleRevert = true;
async function deploy() {
	let defaultAccount;

	if (process.argv[2]) {
		let pk = process.argv[2];
		if (!pk.startsWith('0x')) {
			pk = '0x' + pk;
		}
		// Crea l'account dalla chiave privata e lo aggiunge al wallet di Web3
		const account = web3.eth.accounts.privateKeyToAccount(pk);
		web3.eth.accounts.wallet.add(account);
		defaultAccount = account.address;
		console.log('Deployer account (from private key):', defaultAccount);
	} else {
		// Fallback al metodo originale se non viene passata la chiave
		const providersAccounts = await web3.eth.getAccounts();
		defaultAccount = providersAccounts[0];
		console.log('Deployer account (from node):', defaultAccount);
	}

	const contractDeployer = myContract.deploy({
		data: '0x' + bytecode
	});
	const gas = 3000000;

	const tx = await contractDeployer.send({
		from: defaultAccount,
		gas: gas,
		gasPrice: await web3.eth.getGasPrice(),
	});

	console.log('Contract deployed at address: ' + tx.options.address);
	const deployedAddressPath = '/home/cometbft/data/contract_address.txt';
	const deployedAbiPath = '/home/cometbft/data/contract_abi.json';
	fs.writeFileSync(deployedAddressPath, tx.options.address);
	fs.writeFileSync(deployedAbiPath, JSON.stringify(abi, null, '\t'));
}

deploy()
	.then(() => {
		console.log('Contract deployed successfully.');
	})
	.catch(err => {
		console.error('Error deploying contract:', err);
		process.exitCode = 1;
	})
	.finally(() => {
		console.log('Deployment process finished.');
		if (web3.provider && typeof web3.provider.disconnect === 'function') {
			try { web3.provider.disconnect(); } catch (_) { }
		}
	});
