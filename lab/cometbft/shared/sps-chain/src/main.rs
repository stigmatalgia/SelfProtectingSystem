mod config;
mod types;
mod ledger;
mod api;
mod forwarder;

use std::sync::{Arc, RwLock};
use std::time::Duration; // Added missing import for backoff logic
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
}

impl SpsAbciApp {
    fn process_vote_bytes(&self, tx_data: &[u8]) -> Result<(), String> {
        if tx_data.is_empty() {
            return Ok(());
        }

        let vote: VoteTx = serde_json::from_slice(tx_data)
            .map_err(|e| format!("JSON Decode Error: {}. Data: {}", e, hex::encode(tx_data)))?;

        log::debug!("[ABCI] Processing Vote from agent: {}", vote.agent_id);

        let mut ledger_guard = self.ledger.write().unwrap();
        let action_opt =
            ledger_guard.propose_new_values(&vote.agent_id, &vote.parameters, &vote.values);

        if let Some(event) = action_opt {
            log::info!("[ABCI] BFT CONSENSUS REACHED: triggering action {:?}", event);
            let _ = self.action_tx.send(event);
        }

        Ok(())
    }

    /// Pure validation for CheckTx (no ledger locking)
    fn validate_vote_bytes(&self, tx_data: &[u8]) -> Result<(), String> {
        if tx_data.is_empty() {
            return Ok(());
        }
        let _: VoteTx = serde_json::from_slice(tx_data)
            .map_err(|e| format!("ABCI CheckTx Validation Failed: {}", e))?;
        Ok(())
    }
}

impl Info for SpsAbciApp {
    fn info(&self, _info_request: RequestInfo) -> ResponseInfo {
        log::info!("[ABCI] Info requested -- connection healthy");
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
        log::info!("[ABCI] InitChain -- genesis state established");
        ResponseInitChain::default()
    }

    fn begin_block(&self, begin_block_request: RequestBeginBlock) -> ResponseBeginBlock {
        let height = begin_block_request
            .header
            .as_ref()
            .map(|h| h.height)
            .unwrap_or_default();
        log::debug!("[ABCI] BeginBlock at height {}", height);
        ResponseBeginBlock::default()
    }

    fn deliver_tx(&self, deliver_tx_request: RequestDeliverTx) -> ResponseDeliverTx {
        let mut resp = ResponseDeliverTx::default();
        if let Err(msg) = self.process_vote_bytes(&deliver_tx_request.tx) {
            log::error!("[ABCI] {}", msg);
            resp.code = 1;
            resp.log = msg;
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
        // Stateless validation to avoid write lock contention
        if let Err(msg) = self.validate_vote_bytes(&check_tx_request.tx) {
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

    let (action_tx, _) = broadcast::channel::<crate::types::ActionEvent>(100_000);

    let abci_app = SpsAbciApp {
        ledger: ledger.clone(),
        action_tx: action_tx.clone(),
        fast_checktx: true,
    };
    
    let abci_addr: std::net::SocketAddr = "127.0.0.1:26658".parse().unwrap();
    let abci_app_clone = abci_app.clone();
    
    std::thread::Builder::new()
        .name("abci-server".to_string())
        .spawn(move || {
            log::info!("[ABCI] Server starting on {} (v0.1 runtime)...", abci_addr);
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
            forwarder::run_forwarder(rx, url).await;
        });
    }

    // ── Start Bulletproof CometBFT Queue Worker with Exponential Backoff ──
    let (tx_queue_tx, mut tx_queue_rx) = mpsc::channel::<VoteTx>(100_000);
    
    let comet_rpc_url = cfg.comet.rpc_url.clone();
    let worker_http_client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .pool_max_idle_per_host(100) 
        .build()
        .expect("failed to build HTTP client");
        
    tokio::spawn(async move {
        log::info!("[WORKER] Started background CometBFT transaction queue processor");
        let sem = std::sync::Arc::new(tokio::sync::Semaphore::new(100));

        while let Some(vote) = tx_queue_rx.recv().await {
            if let Ok(tx_json) = serde_json::to_string(&vote) {
                let tx_param = format!("\"{}\"", tx_json.replace('"', "\\\""));
                let url = format!("{}/broadcast_tx_async", comet_rpc_url);
                let client = worker_http_client.clone();
                let permit = sem.clone().acquire_owned().await.unwrap();

                tokio::spawn(async move {
                    let mut backoff = 100; // ms
                    loop {
                        match client.get(&url).query(&[("tx", &tx_param)]).send().await {
                            Ok(resp) => {
                                let body = resp.text().await.unwrap_or_default();
                                if body.contains("\"error\":") || body.contains("mempool is full") {
                                    tokio::time::sleep(Duration::from_millis(backoff)).await;
                                    backoff = std::cmp::min(backoff * 2, 2000); 
                                } else {
                                    break; 
                                }
                            }
                            Err(_) => {
                                tokio::time::sleep(Duration::from_millis(backoff)).await;
                                backoff = std::cmp::min(backoff * 2, 2000);
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
    };

    let app = api::make_router(api_state);
    let listener = tokio::net::TcpListener::bind(&cfg.node.api_addr).await.unwrap();
    log::info!("[MAIN] HTTP API listening on {}", cfg.node.api_addr);
    axum::serve(listener, app).await.unwrap();
}
