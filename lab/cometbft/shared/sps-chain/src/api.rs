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
    pub committed_tx_count: Arc<AtomicU64>,
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
        .route("/stress", post(handle_stress))
        .route("/votes", get(handle_votes))
        .route("/state", get(handle_state))
        .route("/stats", get(handle_stats))
        .route("/tx_count", get(handle_tx_count))
        .route("/alive", get(handle_alive))
        .route("/config/dedup", post(handle_config_dedup))
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
    if std::path::Path::new("/shared/disable_negative_alerts").exists() {
        return StatusCode::OK;
    }
    if alerts.is_empty() { return StatusCode::OK; }
    
    let agent_id = alerts[0].ids.clone();
    log::info!("[API] Received batch of {} alerts from agent {}", alerts.len(), agent_id);

    {
        let mut stats = s.local_stats.write().unwrap();
        stats.received += alerts.len() as u64;
    }

    let mut param_map: HashMap<String, u64> = HashMap::new();
    let mut is_safe_env = false;

    for alert in alerts {
        let alert_type = alert.r#type.to_uppercase();
        if alert_type == "SAFE_ENVIRONMENT" {
            is_safe_env = true;
        } else if VALID_ALERTS.contains(&alert_type.as_str()) {
            *param_map.entry(alert_type).or_insert(0) += alert.value;
        }
    }

    // SAFE_ENVIRONMENT forces all attack parameters to 0
    if is_safe_env {
        for &p in VALID_ALERTS {
            if p != "SAFE_ENVIRONMENT" {
                param_map.insert(p.to_string(), 0);
            }
        }
    }

    if param_map.is_empty() { return StatusCode::OK; }

    let timestamp_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;

    let seq_num = s.seq.fetch_add(1, Ordering::SeqCst);
    
    // Map agent_id string to u32 for the optimized VoteTx
    let agent_id_u32 = if agent_id.len() >= 8 && agent_id.chars().all(|c| c.is_ascii_hexdigit()) {
        u32::from_str_radix(&agent_id[..8], 16).unwrap_or(0)
    } else {
        // Simple hash for string-based IDs like "snort", "suricata", "zeek"
        let mut h = 0u32;
        for (i, b) in agent_id.as_bytes().iter().enumerate() {
            h = h.wrapping_add((*b as u32).wrapping_shl(i as u32 % 4));
        }
        h
    };

    // Build bitmask
    let mut param_mask = 0u32;
    let mut param_values = [0u64; 4];
    for (i, &p) in ["SQL_INJECTION", "XSS_ATTACK", "PATH_TRAVERSAL", "COMMAND_INJECTION"].iter().enumerate() {
        if let Some(&v) = param_map.get(p) {
            param_mask |= 1 << i;
            param_values[i] = v;
        }
    }

    let vote = VoteTx {
        id: seq_num,
        agent_id: agent_id_u32,
        param_mask,
        values: param_values,
        timestamp_ms,
    };

    match s.tx_queue.send(vote).await {
        Ok(_) => {
            let mut stats = s.local_stats.write().unwrap();
            stats.processed += 1;
            StatusCode::ACCEPTED
        },
        Err(e) => {
            log::error!("[API] Queue full or closed! Failed to queue vote: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        }
    }
}

async fn handle_stress(
    State(s): State<ApiState>,
    Json(alert): Json<IncomingAlert>,
) -> impl IntoResponse {
    let mut parameters = Vec::new();
    let mut values = Vec::new();
    
    parameters.push(alert.r#type.to_uppercase());
    values.push(alert.value);

    let timestamp_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;

    let seq_num = s.seq.fetch_add(1, Ordering::SeqCst);
    let agent_id_u32 = u32::from_str_radix(&s.node_id[..8], 16).unwrap_or(0);

    let mut param_mask = 0u32;
    let mut param_values = [0u64; 4];
    let alert_type = alert.r#type.to_uppercase();
    for (i, &p) in ["SQL_INJECTION", "XSS_ATTACK", "PATH_TRAVERSAL", "COMMAND_INJECTION"].iter().enumerate() {
        if p == alert_type {
            param_mask |= 1 << i;
            param_values[i] = alert.value;
        }
    }

    let vote = VoteTx {
        id: seq_num,
        agent_id: agent_id_u32,
        param_mask,
        values: param_values,
        timestamp_ms,
    };

    let _ = s.tx_queue.send(vote).await;
    StatusCode::ACCEPTED
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
    let count = s.committed_tx_count.load(Ordering::Relaxed);
    Json(TxCount { count })
}


async fn handle_alive(State(s): State<ApiState>) -> impl IntoResponse {
    Json(crate::types::AliveResponse {
        status: "alive".to_string(),
        id: s.node_id.clone(),
        role: s.role.clone(),
        account: "".to_string(), // Add dummy account field
    })
}

#[derive(serde::Deserialize)]
struct DedupParams {
    enabled: bool,
}

async fn handle_config_dedup(
    State(s): State<ApiState>,
    axum::extract::Query(params): axum::extract::Query<DedupParams>,
) -> impl IntoResponse {
    let mut ledger = s.ledger.write().unwrap();
    ledger.dedup_disabled = !params.enabled;
    log::info!("[API] Deduplication set to: {}", params.enabled);
    StatusCode::OK
}