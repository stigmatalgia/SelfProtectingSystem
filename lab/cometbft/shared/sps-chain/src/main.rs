mod config;
mod types;
mod ledger;
mod api;
mod forwarder;

use std::sync::{Arc, RwLock, Mutex};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use tokio::sync::{broadcast, mpsc};
use crate::config::NodeConfig;
use crate::types::{ActionEvent, VoteTx};
use crate::ledger::Ledger;
use crate::api::ApiState;
use abci::sync_api::{Consensus, Info, Mempool, Snapshot};
use abci::types::{
    RequestBeginBlock, RequestCheckTx, RequestCommit, RequestDeliverTx, RequestEndBlock,
    RequestInfo, RequestInitChain, ResponseBeginBlock, ResponseCheckTx, ResponseCommit,
    ResponseDeliverTx, ResponseEndBlock, ResponseInfo, ResponseInitChain,
};

#[derive(Clone)]
struct SpsAbciApp {
    ledger: Arc<RwLock<Ledger>>,
    action_tx: broadcast::Sender<ActionEvent>,
    fast_checktx: bool,
    committed_tx_count: Arc<AtomicU64>,
}

impl SpsAbciApp {
    /// Decode bincode bytes (extremely fast compared to JSON)
    fn decode_tx(&self, tx_data: &[u8]) -> Result<VoteTx, String> {
        if tx_data.is_empty() {
            return Err("Empty transaction".to_string());
        }
        bincode::deserialize(tx_data)
            .map_err(|e| format!("Bincode Decode Error: {}. Data len: {}", e, tx_data.len()))
    }
}

impl Info for SpsAbciApp {
    fn info(&self, _info_request: RequestInfo) -> ResponseInfo {
        ResponseInfo {
            data: "sps-node".to_string(),
            version: "0.1".to_string(),
            app_version: 1,
            last_block_height: 0,
            last_block_app_hash: vec![],
        }
    }
}

impl Consensus for SpsAbciApp {
    fn init_chain(&self, _init_chain_request: RequestInitChain) -> ResponseInitChain {
        ResponseInitChain::default()
    }

    fn begin_block(&self, _begin_block_request: RequestBeginBlock) -> ResponseBeginBlock {
        ResponseBeginBlock::default()
    }

    fn deliver_tx(&self, deliver_tx_request: RequestDeliverTx) -> ResponseDeliverTx {
        let mut resp = ResponseDeliverTx::default();
        match self.decode_tx(&deliver_tx_request.tx) {
            Ok(vote) => {
                let action_opt = {
                    let mut ledger_guard = self.ledger.write().unwrap();
                    ledger_guard.propose_new_values(vote.agent_id, vote.param_mask, &vote.values)
                };
                
                if let Some(event) = action_opt {
                    log::info!("[ABCI] EVENT-DRIVEN CONSENSUS REACHED: triggering action {:?}", event);
                    let _ = self.action_tx.send(event);
                }
                
                self.committed_tx_count.fetch_add(1, Ordering::Relaxed);
            }
            Err(msg) => {
                log::error!("[ABCI] {}", msg);
                resp.code = 1;
                resp.log = msg;
            }
        }
        resp
    }

    fn end_block(&self, _end_block_request: RequestEndBlock) -> ResponseEndBlock {
        ResponseEndBlock::default()
    }

    fn commit(&self, _commit_request: RequestCommit) -> ResponseCommit {
        ResponseCommit::default()
    }
}

impl Mempool for SpsAbciApp {
    fn check_tx(&self, check_tx_request: RequestCheckTx) -> ResponseCheckTx {
        if self.fast_checktx {
            return ResponseCheckTx::default();
        }
        let mut resp = ResponseCheckTx::default();
        if let Err(msg) = self.decode_tx(&check_tx_request.tx) {
            log::error!("[ABCI] CheckTx Failed: {}", msg);
            resp.code = 1;
            resp.log = msg;
        }
        resp
    }
}

impl Snapshot for SpsAbciApp {}

#[tokio::main]
async fn main() {
    env_logger::Builder::from_env(
        env_logger::Env::default().default_filter_or("warn")
    ).init();

    let config_path = std::env::args()
        .zip(std::env::args().skip(1))
        .find(|(k, _)| k == "--config")
        .map(|(_, v)| v)
        .unwrap_or_else(|| "/data/config.toml".to_string());

    let cfg = NodeConfig::load(&config_path).unwrap_or_else(|e| {
        eprintln!("FATAL: Cannot load config {}: {}", config_path, e);
        std::process::exit(1);
    });

    log::info!(
        "[MAIN] sps-node starting in ABCI mode — id={} role={} api={}",
        cfg.node.id, cfg.node.role, cfg.node.api_addr
    );

    let mut ledger = Ledger::new(cfg.ledger.parameters.clone(), cfg.ledger.agents_count);
    ledger.dedup_disabled = cfg.ledger.disable_dedup;
    ledger.populate_action_map();
    let ledger = Arc::new(RwLock::new(ledger));
    
    let committed_tx_count = Arc::new(AtomicU64::new(0));
    let (action_tx, _) = broadcast::channel::<crate::types::ActionEvent>(100_000);

    let abci_app = SpsAbciApp {
        ledger: ledger.clone(),
        action_tx: action_tx.clone(),
        fast_checktx: true,
        committed_tx_count: committed_tx_count.clone(),
    };
    
    let abci_addr: std::net::SocketAddr = "127.0.0.1:26658".parse().unwrap();
    let abci_app_clone = abci_app.clone();
    
    std::thread::Builder::new()
        .name("abci-server".to_string())
        .spawn(move || {
            log::info!("[ABCI] Server starting on {}...", abci_addr);
            let server = abci::sync_api::Server::new(
                abci_app_clone.clone(),
                abci_app_clone.clone(),
                abci_app_clone.clone(),
                abci_app_clone,
            );
            if let Err(e) = server.run(abci_addr) {
                log::error!("[ABCI] Server stopped with error: {}", e);
            }
        })
        .expect("Failed to spawn ABCI thread");

    if cfg.is_fullnode() || cfg.node.role == "validator" {
        let rx = action_tx.subscribe();
        let url = cfg.actuator.url.clone();
        tokio::spawn(async move {
            log::info!("[FWD] Forwarder monitoring /shared/disable_feedback...");
            forwarder::run_forwarder(rx, url).await;
        });
    }

    // ── Optimized background CometBFT transaction queue processor ──
    let (tx_queue_tx, mut tx_queue_rx) = mpsc::channel::<VoteTx>(100_000);
    
    let comet_rpc_url = cfg.comet.rpc_url.clone();
    let worker_http_client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .pool_max_idle_per_host(100) 
        .build()
        .expect("failed to build HTTP client");
        
    tokio::spawn(async move {
        log::info!("[WORKER] Started background CometBFT transaction queue processor");
        let sem = std::sync::Arc::new(tokio::sync::Semaphore::new(200));

        while let Some(vote) = tx_queue_rx.recv().await {
            // Use Bincode + Hex for transport to CometBFT
            if let Ok(tx_bin) = bincode::serialize(&vote) {
                let _tx_hex = hex::encode(&tx_bin); 
                // Actually, the broadcast_tx RPC usually expects hex for the 'tx' parameter if not using JSON.
                // But wait, the previous code used JSON and escaped quotes.
                // Let's use hex for simplicity as it's safe.
                let tx_hex = hex::encode(tx_bin);
                let url = format!("{}/broadcast_tx_async", comet_rpc_url);
                let client = worker_http_client.clone();
                let permit = sem.clone().acquire_owned().await.unwrap();

                tokio::spawn(async move {
                    let mut backoff = 50; // ms
                    loop {
                        match client.get(&url).query(&[("tx", &format!("0x{}", tx_hex))]).send().await {
                            Ok(resp) => {
                                if resp.status().is_success() {
                                    break;
                                }
                                tokio::time::sleep(Duration::from_millis(backoff)).await;
                                backoff = std::cmp::min(backoff * 2, 1000); 
                            }
                            Err(_) => {
                                tokio::time::sleep(Duration::from_millis(backoff)).await;
                                backoff = std::cmp::min(backoff * 2, 1000);
                            }
                        }
                    }
                    drop(permit);
                });
            }
        }
    });

    let api_state = ApiState {
        node_id: cfg.node.id.clone(),
        role: cfg.node.role.clone(),
        ledger: ledger.clone(),
        seq: Arc::new(std::sync::atomic::AtomicU64::new(0)),
        local_stats: Arc::new(RwLock::new(api::stats::LocalStats::default())),
        action_tx: action_tx.clone(),
        tx_queue: tx_queue_tx,
        committed_tx_count,
    };

    let app = api::make_router(api_state);
    let listener = tokio::net::TcpListener::bind(&cfg.node.api_addr).await.unwrap();
    log::info!("[MAIN] HTTP API listening on {}", cfg.node.api_addr);
    axum::serve(listener, app).await.unwrap();
}
