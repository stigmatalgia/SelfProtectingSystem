use serde::Deserialize;
use std::fs;

#[derive(Debug, Clone, Deserialize)]
pub struct NodeConfig {
    pub node: NodeSection,
    #[serde(default)]
    pub consensus: ConsensusSection,
    pub ledger: LedgerSection,
    #[serde(default)]
    pub comet: CometSection,
    #[serde(default)]
    pub peers: PeersSection,
    #[serde(default)]
    pub actuator: ActuatorSection,
}

#[derive(Debug, Clone, Deserialize)]
pub struct NodeSection {
    pub id: String,
    /// "validator" | "agent" | "fullnode"
    pub role: String,
    #[serde(default)]
    pub listen_addr: String,
    pub api_addr: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CometSection {
    #[serde(default = "default_comet_rpc_url")]
    pub rpc_url: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ConsensusSection {
    #[serde(default = "default_200")]
    pub timeout_propose_ms: u64,
    #[serde(default = "default_200")]
    pub timeout_commit_ms: u64,
    #[serde(default = "default_10000")]
    pub block_max_txs: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct LedgerSection {
    pub parameters: Vec<String>,
    pub agents_count: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PeersSection {
    /// Format: "id@ip:port" or "ip:port"
    #[serde(default)]
    pub persistent: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct ActuatorSection {
    #[serde(default)]
    pub url: String,
}

fn default_200() -> u64 { 200 }
fn default_10000() -> usize { 10000 }
fn default_comet_rpc_url() -> String {
    std::env::var("COMET_RPC_URL").unwrap_or_else(|_| "http://10.99.0.1:26657".to_string())
}

impl Default for ConsensusSection {
    fn default() -> Self {
        Self {
            timeout_propose_ms: 200,
            timeout_commit_ms: 200,
            block_max_txs: 1000000,
        }
    }
}

impl Default for CometSection {
    fn default() -> Self {
        Self {
            rpc_url: default_comet_rpc_url(),
        }
    }
}

impl Default for PeersSection {
    fn default() -> Self { Self { persistent: vec![] } }
}

impl NodeConfig {
    pub fn load(path: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let content = fs::read_to_string(path)?;
        let config: NodeConfig = toml::from_str(&content)?;
        Ok(config)
    }

    /// Returns only the "ip:port" portion of each peer address (strips "id@" prefix).
    pub fn peer_addrs(&self) -> Vec<String> {
        self.peers.persistent.iter().map(|p| {
            if let Some(at) = p.find('@') {
                p[at + 1..].to_string()
            } else {
                p.clone()
            }
        }).collect()
    }

    pub fn is_agent(&self) -> bool { self.node.role == "agent" }
    pub fn is_fullnode(&self) -> bool { self.node.role == "fullnode" }
}
