// SPDX-License-Identifier: MIT
pragma solidity 0.8.19;
//import "@openzeppelin/contracts/utils/Strings.sol";
import "./Strings.sol";
 
contract IDS {


	struct Parameter_Status {
		uint256 currentValue; // Valore corrente
		mapping(address => uint256) agentProposedValue; // Mappa per agente (indirizzo) -> proposta
		// Mappa per aggiornare in modo incrementale il conteggio di ciascun valore proposto
        mapping(uint256 => uint256) voteCount;
        // Indica se un agente ha già votato per questo parametro
        mapping(address => bool) hasVoted;
	}
	
	string[] state_parameters = [#PARAMS];
	// Mappatura per controllare rapidamente se un parametro esiste
    mapping(string => bool) private state_parameters_exist;


	address[] agents = [#AGENTS];
	uint256 agents4Params = #NUMAGENTS4PARAMS;

	mapping(bytes32 => string) private stateAction;
	
	// Stato del contratto, memorizza tutti i parametri e i relativi stati
	mapping(string => Parameter_Status) public statusMapDT;
	
	event ActionRequired(address selectedAgent, string actionToApply);

	address owner;

	constructor(){
		owner = msg.sender;
		for (uint256 i =0; i < state_parameters.length; i++){
			state_parameters_exist[state_parameters[i]] = true;
 		}
	}

	modifier onlyOwner {
		require(msg.sender == owner);
		_;
    }

	modifier lengthCheck(uint256 lengthA, uint256 lengthB){
		require(lengthA == lengthB, "Error: Mismatch between the lengths");
		_;
 	}

	function changeOwner(address newOwner) onlyOwner external{
		require(newOwner != address(0));
		owner = newOwner;
	}

	function insertMap(string[] memory states, string[] memory actions) onlyOwner lengthCheck(states.length, actions.length) external {
		for (uint256 i =0; i < states.length; i++){
			bytes32 stateHash = keccak256(abi.encodePacked(states[i]));
			stateAction[stateHash] = actions[i];
 		}
	}

	// Metodo per proporre nuovi valori
	function proposeNewValues(string[] memory parameters, uint256[] memory values) lengthCheck(parameters.length, values.length) external {
		bool change = false;

		for (uint256 i = 0; i < parameters.length; i++) {
			string memory parameter = parameters[i];
			
			require(state_parameters_exist[parameter], string.concat('Parameter not Exist: ', parameter));
			uint256 newValue = values[i];

			Parameter_Status storage Pstatus = statusMapDT[parameter];

			// Se l'agente ha già votato, oggiorno il conteggio decrementando il voto precedente.
            if (Pstatus.hasVoted[msg.sender]) {
                uint256 previousVote = Pstatus.agentProposedValue[msg.sender];
                // Se il nuovo voto è uguale al precedente, nulla da aggiornare
                if (previousVote == newValue) {
                    continue;
                }
                if (Pstatus.voteCount[previousVote] > 0) {
                    Pstatus.voteCount[previousVote]--;
                }
            } else {
                // E' il primo voto
                Pstatus.hasVoted[msg.sender] = true;
            }

			// Registra il nuovo voto e aggiorna il conteggio
            Pstatus.agentProposedValue[msg.sender] = newValue;
            Pstatus.voteCount[newValue]++;

            // Se il numero di voti per il nuovo valore supera la soglia, aggiorna il valore corrente
            if (Pstatus.voteCount[newValue] > (agents4Params / 2)) {
                if (Pstatus.currentValue != newValue) {
                    Pstatus.currentValue = newValue;
                    change = true;
                }
            }
		}


		if(change){
			bytes memory stateBytes = "";
			for(uint256 j = 0; j < state_parameters.length; j++){
				uint256 val = statusMapDT[state_parameters[j]].currentValue;
				stateBytes = abi.encodePacked(stateBytes, Strings.toString(val));
			}
			bytes32 stateHash = keccak256(stateBytes);
            string memory action = stateAction[stateHash];
			if(bytes(action).length != 0){
				uint256 indiceAgente = uint256(keccak256(abi.encodePacked(block.timestamp, block.number))) % agents.length;
				address agente = agents[indiceAgente];
				emit  ActionRequired(agente, action);
			}
		}

	}

}
