const solc = require('solc');
const path = require('path');
const fs = require('fs');
const { Web3 } = require('web3');
const { IpcProvider } = require('web3-providers-ipc');

const contractName = 'IDS';
const fileName = `${contractName}.sol`;
// Read the Solidity source code from the file system
const contractPath = path.join(__dirname, fileName);
const sourceCode = fs.readFileSync(contractPath, 'utf8');
// solc compiler config
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


function findImportsLoc(importPath) {
	try {
		const resolvedPath = path.resolve('/home/qbft/data/contract', importPath);
		return { contents: fs.readFileSync(resolvedPath, 'utf8') };
	} catch (e) {
		return { error: `File not found: ${importPath}` };
	}
}

const compiledCode = JSON.parse(solc.compile(JSON.stringify(input), { import: findImportsLoc }));
console.log(compiledCode)
str = JSON.stringify(compiledCode.contracts[fileName], null, 4); //
//console.log(str)

const bytecode = compiledCode.contracts[fileName][contractName].evm.bytecode.object;

//console.log('Contract Bytecode:\n', bytecode);

// Get the ABI from the compiled contract
const abi = compiledCode.contracts[fileName][contractName].abi;

//console.log('Contract ABI:\n', JSON.stringify(abi));
//console.log('end ABI')
var web3 = new Web3(new IpcProvider('/home/qbft/data/geth.ipc'));

const myContract = new web3.eth.Contract(abi);
myContract.handleRevert = true;

async function deploy() {
	const providersAccounts = await web3.eth.getAccounts();
	const defaultAccount = providersAccounts[0];
	//console.log('Deployer account:', defaultAccount);
	const contractDeployer = myContract.deploy({
		data: '0x' + bytecode
	});
	const gas = 3000000;
	//const gas = await contractDeployer.estimateGas({
	//		from: defaultAccount,
	//	});
	//console.log('Estimated gas:', gas);

	const tx = await contractDeployer.send({
		from: defaultAccount,
		gas: gas,
		gasPrice: await web3.eth.getGasPrice(),
	});
	console.log('Contract deployed at address: ' + tx.options.address);
	const deployedAddressPath = '/home/qbft/data/contract_address.txt';
	const deployedAbiPath = '/home/qbft/data/contract_abi.json';
	fs.writeFileSync(deployedAddressPath, tx.options.address);
	fs.writeFileSync(deployedAbiPath, JSON.stringify(abi, null, '\t'));
}

deploy()
	.then(() => {
		console.log('Contract deployed ');
	})
	.catch(err => {
		console.error('Error deploying contract:', err);
	})
	.finally(() => {
		console.log('Deployment process finished.');
		web3.provider.disconnect();
	});

