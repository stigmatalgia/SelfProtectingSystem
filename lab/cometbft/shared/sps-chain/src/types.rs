use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VoteTx {
    pub id: u64,             
    pub agent_id: u32,       
    pub param_mask: u32,     
    pub values: [u64; 4],    // FIXED SIZE ARRAY: Zero heap allocations!
    pub timestamp_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActionEvent {
    pub action: String,
    pub state: String,
    pub timestamp_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TxCount {
    pub count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AliveResponse {
    pub status: String,
    pub id: String,
    pub role: String,
    pub account: String,
}
