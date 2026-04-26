/// HTTP API server — axum-based, mirrors the blockchain_api.js interface exactly.
use axum::{
    Router,
    extract::State,
    response::IntoResponse,
    http::StatusCode,
    routing::{get, post},
    Json,
};
use std::sync::{Arc, RwLock, atomic::{AtomicU64, Ordering}};
use std::collections::HashMap;
use tokio::sync::{broadcast, mpsc};
use crate::types::{TxCount, VoteTx};
use crate::ledger::Ledger;
use crate::types::ActionEvent;

const VALID_ALERTS: &[&str] = &[
    "SQL_INJECTION",
    "XSS_ATTACK",
    "PATH_TRAVERSAL",
    "COMMAND_INJECTION",
    "SAFE_ENVIRONMENT",
];

#[derive(Clone)]
pub struct ApiState {
    pub node_id: String,
    pub role: String,
    pub ledger: Arc<RwLock<Ledger>>,
    pub seq: Arc<AtomicU64>,
    pub local_stats: Arc<RwLock<stats::LocalStats>>,
    pub action_tx: broadcast::Sender<ActionEvent>,
    pub tx_queue: mpsc::Sender<VoteTx>,
}

pub mod stats {
    use super::*;
    #[derive(Default)]
    pub struct LocalStats {
        pub received: u64,
        pub processed: u64,
        pub last_voted: HashMap<String, u64>,
    }
}

pub fn make_router(state: ApiState) -> Router {
    Router::new()
        .route("/alert", post(handle_alert))
        .route("/votes", get(handle_votes))
        .route("/state", get(handle_state))
        .route("/stats", get(handle_stats))
        .route("/tx_count", get(handle_tx_count))
        .route("/alive", get(handle_alive))
        .with_state(state)
}

#[derive(Debug, serde::Deserialize)]
struct IncomingAlert {
    pub ids: String,
    pub r#type: String,
    pub value: u64,
}

async fn handle_alert(
    State(s): State<ApiState>,
    Json(alerts): Json<Vec<IncomingAlert>>,
) -> impl IntoResponse {
    if alerts.is_empty() { return StatusCode::OK; }
    
    let agent_id = alerts[0].ids.clone();
    log::info!("[API] Received batch of {} alerts from agent {}", alerts.len(), agent_id);

    let mut param_map: HashMap<String, u64> = HashMap::new();
    for alert in alerts {
        let alert_type = alert.r#type.to_uppercase();
        if VALID_ALERTS.contains(&alert_type.as_str()) {
            *param_map.entry(alert_type).or_insert(0) += alert.value;
        }
    }

    if param_map.is_empty() { return StatusCode::OK; }

    let mut parameters = Vec::new();
    let mut values = Vec::new();
    for (p, v) in param_map {
        parameters.push(p);
        values.push(v);
    }

    let timestamp_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;

    let seq_num = s.seq.fetch_add(1, Ordering::SeqCst);

    let vote = VoteTx {
        id: format!("{}:{}", agent_id, seq_num),
        agent_id: agent_id.clone(),
        parameters,
        values,
        timestamp_ms,
    };

    match s.tx_queue.send(vote).await {
        Ok(_) => {
            StatusCode::ACCEPTED
        },
        Err(e) => {
            log::error!("[API] Queue full or closed! Failed to queue vote: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        }
    }
}

async fn handle_votes(State(s): State<ApiState>) -> impl IntoResponse {
    let ledger = s.ledger.read().unwrap();
    Json(ledger.get_votes())
}

async fn handle_state(State(s): State<ApiState>) -> impl IntoResponse {
    let ledger = s.ledger.read().unwrap();
    serde_json::json!({ "state": ledger.get_state() }).to_string()
}

async fn handle_stats(State(s): State<ApiState>) -> impl IntoResponse {
    let stats = s.local_stats.read().unwrap();
    Json(serde_json::json!({
        "totalAlertsReceived": stats.received,
        "totalAlertsProcessed": stats.processed,
    }))
}

async fn handle_tx_count(State(s): State<ApiState>) -> impl IntoResponse {
    let ledger = s.ledger.read().unwrap();
    Json(TxCount { count: ledger.tx_count() })
}

async fn handle_alive(State(s): State<ApiState>) -> impl IntoResponse {
    Json(crate::types::AliveResponse {
        status: "alive".to_string(),
        node_id: s.node_id.clone(),
        role: s.role.clone(),
    })
}