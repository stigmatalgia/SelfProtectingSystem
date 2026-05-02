/// IDS voting state machine — optimized for extreme throughput.
use std::collections::HashMap;
use sha2::{Sha256, Digest};
use crate::types::ActionEvent;

#[derive(Debug, Default)]
struct ParamStatus {
    current_value: u64,
    agent_proposed: HashMap<u32, u64>, // agent_id (u32) -> proposed value
    vote_count: HashMap<u64, usize>,   // value -> number of votes
}

pub struct Ledger {
    parameters: Vec<String>,
    param_to_idx: HashMap<String, usize>,
    status_map: Vec<ParamStatus>,
    state_action: HashMap<String, String>, // sha256(state_str) -> action
    agents_count: usize,
    committed_txs: u64,
    pub dedup_disabled: bool,
    
    // Internal registry to avoid string keys in maps
    agent_registry: HashMap<String, u32>,
    next_agent_id: u32,
}

impl Ledger {
    pub fn new(parameters: Vec<String>, agents_count: usize) -> Self {
        let mut param_to_idx = HashMap::new();
        let mut status_map = Vec::with_capacity(parameters.len());
        for (idx, p) in parameters.iter().enumerate() {
            param_to_idx.insert(p.clone(), idx);
            status_map.push(ParamStatus::default());
        }
        Self {
            parameters,
            param_to_idx,
            status_map,
            state_action: HashMap::new(),
            agents_count,
            committed_txs: 0,
            dedup_disabled: false,
            agent_registry: HashMap::with_capacity(agents_count),
            next_agent_id: 0,
        }
    }

    /// Build the 4-bit state → action map.
    pub fn populate_action_map(&mut self) {
        let n = self.parameters.len();
        let max_val = (1 << n) - 1;

        for i in 1..=max_val {
            let mut actions = Vec::new();
            let mut state_bits = Vec::new();

            for (idx, param) in self.parameters.iter().enumerate() {
                let bit_pos = n - 1 - idx;
                let bit_val = 1 << bit_pos;
                let is_set = (i & bit_val) != 0;
                
                state_bits.push(if is_set { "1" } else { "0" });

                if is_set {
                    match param.as_str() {
                        "SQL_INJECTION" => actions.push("echo SQL Injection detected"),
                        "XSS_ATTACK" => actions.push("echo XSS Attack detected"),
                        "PATH_TRAVERSAL" => actions.push("echo Path Traversal detected"),
                        "COMMAND_INJECTION" => actions.push("echo Command Injection detected"),
                        _ => {}
                    }
                }
            }

            let state_str: String = state_bits.concat();
            let action_str = actions.join(" && ");
            
            if !action_str.is_empty() {
                let hash = hex::encode(Sha256::digest(state_str.as_bytes()));
                self.state_action.insert(hash, action_str);
            }
        }
        log::info!("[LEDGER] Action map ready — {} entries", self.state_action.len());
    }

    /// Process a vote from an agent using a pre-optimized bitmask.
    pub fn propose_new_values(
        &mut self,
        agent_id: u32,
        param_mask: u32,
        values: &[u64; 4],
    ) -> Option<ActionEvent> {
        let dedup_disabled = self.dedup_disabled;
        let mut changed = false;
        let threshold = self.agents_count / 2;

        // Process only parameters set in the mask
        for idx in 0..self.parameters.len() {
            if (param_mask & (1 << idx)) == 0 {
                continue;
            }
            
            // Safety: values must match the number of set bits or be long enough
            let new_value = *values.get(idx).unwrap_or(&0);
            let status = &mut self.status_map[idx];

            if let Some(&previous_vote) = status.agent_proposed.get(&agent_id) {
                if previous_vote == new_value && !dedup_disabled {
                    continue;
                }
                if let Some(count) = status.vote_count.get_mut(&previous_vote) {
                    if *count > 0 {
                        *count -= 1;
                    }
                }
            }

            status.agent_proposed.insert(agent_id, new_value);
            *status.vote_count.entry(new_value).or_insert(0) += 1;
            
            let votes = status.vote_count[&new_value];
            if votes > threshold && status.current_value != new_value {
                status.current_value = new_value;
                changed = true;
            }
        }

        self.committed_txs += 1;

        if changed {
            let mut state_str = String::with_capacity(self.parameters.len());
            for status in &self.status_map {
                state_str.push(if status.current_value == 0 { '0' } else { '1' });
            }
            
            let hash = hex::encode(Sha256::digest(state_str.as_bytes()));

            if let Some(action) = self.state_action.get(&hash) {
                let ts = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_millis() as u64;
                return Some(ActionEvent {
                    action: action.clone(),
                    state: state_str,
                    timestamp_ms: ts,
                });
            }
        }
        None
    }

    pub fn tx_count(&self) -> u64 { self.committed_txs }

    /// Restituisce il vettore dei valori correnti CONFERMATI per ogni parametro
    /// (nell'ordine canonico SQL_INJECTION, XSS_ATTACK, PATH_TRAVERSAL, COMMAND_INJECTION).
    /// Valore 0 = sicuro, 1 = attacco confermato via BFT.
    /// Usato dalla dedup API per scartare alert ridondanti rispetto allo stato reale.
    pub fn get_confirmed_values(&self) -> [u64; 4] {
        let mut out = [0u64; 4];
        for i in 0..self.status_map.len().min(4) {
            out[i] = self.status_map[i].current_value;
        }
        out
    }

    pub fn get_state(&self) -> String {
        self.status_map.iter()
            .map(|s| s.current_value.to_string())
            .collect()
    }

    pub fn get_votes(&self) -> serde_json::Value {
        let mut res = serde_json::Map::new();
        for (i, p) in self.parameters.iter().enumerate() {
            let status = &self.status_map[i];
            let mut votes = serde_json::Map::new();
            for (val, count) in &status.vote_count {
                votes.insert(val.to_string(), serde_json::json!(count));
            }
            res.insert(p.clone(), serde_json::Value::Object(votes));
        }
        serde_json::Value::Object(res)
    }
}
