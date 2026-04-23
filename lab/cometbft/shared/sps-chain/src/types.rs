use serde::{Deserialize, Serialize};

/// A vote transaction submitted by an agent node.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VoteTx {
    /// Unique ID: "{agent_id}:{seq_num}"
    pub id: String,
    pub agent_id: String,
    pub parameters: Vec<String>,
    pub values: Vec<u64>,
    pub timestamp_ms: u64,
}

/// Messages exchanged over the P2P gossip layer.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "msg_type", content = "payload")]
pub enum NetworkMsg {
    Vote(VoteTx),
    Ping { from: String },
    Pong { from: String },
}

/// Emitted by the ledger when a majority vote changes state.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActionEvent {
    pub action: String,
    pub state: String,
    pub timestamp_ms: u64,
}

/// Response for GET /stats
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Stats {
    #[serde(rename = "totalAlertsReceived")]
    pub total_alerts_received: u64,
    #[serde(rename = "totalAlertsProcessed")]
    pub total_alerts_processed: u64,
}

/// Response for GET /tx_count
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TxCount {
    pub count: u64,
}

/// Response for GET /alive
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AliveResponse {
    pub status: String,
    pub node_id: String,
    pub role: String,
}

/// Incoming alert from IDS via POST /alert or POST /stress
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AlertRequest {
    #[serde(rename = "type", default)]
    pub alert_type: String,
    #[serde(default = "default_value")]
    pub value: u64,
}

fn default_value() -> u64 {
    1
}
