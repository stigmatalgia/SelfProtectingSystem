mod config;
mod types;
mod ledger;
mod p2p;
mod api;
mod forwarder;

use std::sync::Arc;
use std::sync::atomic::AtomicU64;
use tokio::sync::{RwLock, broadcast};
use crate::config::NodeConfig;
use crate::ledger::Ledger;
use crate::p2p::GossipHandle;
use crate::api::ApiState;

#[tokio::main]
async fn main() {
    env_logger::Builder::from_env(
        env_logger::Env::default().default_filter_or("info")
    ).init();

    // --config <path>  (default: /data/config.toml)
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
        "[MAIN] sps-node starting — id={} role={} api={} p2p={}",
        cfg.node.id, cfg.node.role, cfg.node.api_addr, cfg.node.listen_addr
    );

    // ── Ledger ─────────────────────────────────────────────────────────────
    let mut ledger = Ledger::new(cfg.ledger.parameters.clone(), cfg.ledger.agents_count);
    ledger.populate_action_map();
    let ledger = Arc::new(RwLock::new(ledger));

    // ── Action event channel ────────────────────────────────────────────────
    let (action_tx, _) = broadcast::channel::<crate::types::ActionEvent>(100_000);

    // ── P2P Gossip ─────────────────────────────────────────────────────────
    let gossip = GossipHandle::new(
        cfg.node.id.clone(),
        ledger.clone(),
        action_tx.clone(),
    );

    // Start P2P listener
    {
        let g = gossip.clone();
        let addr = cfg.node.listen_addr.clone();
        tokio::spawn(async move { g.start_listener(addr).await; });
    }

    // Connect to all configured peers (with auto-retry)
    {
        let g = gossip.clone();
        let peers = cfg.peer_addrs();
        tokio::spawn(async move { g.connect_to_peers(peers).await; });
    }

    // ── Actuator forwarder (fullnode only) ──────────────────────────────────
    if cfg.is_fullnode() {
        let rx = action_tx.subscribe();
        let url = cfg.actuator.url.clone();
        tokio::spawn(async move {
            forwarder::run_forwarder(rx, url).await;
        });
    }

    // ── HTTP API ────────────────────────────────────────────────────────────
    let api_state = ApiState {
        node_id: cfg.node.id.clone(),
        role: cfg.node.role.clone(),
        ledger: ledger.clone(),
        gossip: gossip.clone(),
        seq: Arc::new(AtomicU64::new(0)),
        local_stats: Arc::new(RwLock::new(api::LocalStats::default())),
    };

    let app = api::make_router(api_state);
    let listener = tokio::net::TcpListener::bind(&cfg.node.api_addr)
        .await
        .unwrap_or_else(|e| {
            eprintln!("FATAL: Cannot bind API on {}: {}", cfg.node.api_addr, e);
            std::process::exit(1);
        });

    log::info!("[MAIN] HTTP API listening on {}", cfg.node.api_addr);
    axum::serve(listener, app).await.unwrap_or_else(|e| {
        eprintln!("FATAL: API server error: {}", e);
        std::process::exit(1);
    });
}
