/// HTTP API server — axum-based, mirrors the blockchain_api.js interface exactly.
/// Endpoints:
///   POST /alert         — batched alert ingestion (IDS normal mode)
///   POST /stress        — direct high-throughput alert (benchmark mode)
///   GET  /stats         — {totalAlertsReceived, totalAlertsProcessed}
///   GET  /alive         — {status, node_id, role}
///   GET  /tx_count      — {count: N} (new: used by benchmark scripts)
///   POST /admin/populate_map — internal (no-op here, map built at startup)
use axum::{
    Router,
    extract::State,
    response::IntoResponse,
    http::StatusCode,
    routing::{get, post},
};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::collections::HashMap;
use std::path::Path;
use tokio::sync::RwLock;
use crate::types::{AlertRequest, Stats, TxCount, VoteTx};
use crate::ledger::Ledger;
use crate::p2p::GossipHandle;

const VALID_ALERTS: &[&str] = &[
    "SAFE_ENVIRONMENT", "SQL_INJECTION", "XSS_ATTACK",
    "PATH_TRAVERSAL", "COMMAND_INJECTION",
];
const ATTACK_ALERTS: &[&str] = &[
    "SQL_INJECTION", "XSS_ATTACK", "PATH_TRAVERSAL", "COMMAND_INJECTION",
];
const DISABLE_NEGATIVE_MARKER: &str = "/shared/disable_negative_alerts";

#[derive(Clone)]
pub struct ApiState {
    pub node_id: String,
    pub role: String,
    pub ledger: Arc<RwLock<Ledger>>,
    pub gossip: GossipHandle,
    pub seq: Arc<AtomicU64>,
    pub local_stats: Arc<RwLock<LocalStats>>,
}

#[derive(Default)]
pub struct LocalStats {
    pub received: u64,
    pub processed: u64,
    pub last_voted: HashMap<String, u64>,
}

pub fn make_router(state: ApiState) -> Router {
    Router::new()
        .route("/alert",            post(handle_alert))
        .route("/stress",           post(handle_stress))
        .route("/stats",            get(handle_stats))
        .route("/alive",            get(handle_alive))
        .route("/tx_count",         get(handle_tx_count))
        .route("/admin/populate_map", post(handle_populate_map))
        .with_state(state)
}

// POST /alert — immediate mode: one effective alert => one VoteTx propose.
async fn handle_alert(
    State(s): State<ApiState>,
    body: axum::body::Bytes,
) -> impl IntoResponse {
    let benchmark_mode = Path::new(DISABLE_NEGATIVE_MARKER).exists();

    let raw: serde_json::Value = match serde_json::from_slice(&body) {
        Ok(v) => v,
        Err(_) => return (StatusCode::BAD_REQUEST, serde_json::json!({"error": "Invalid JSON"}).to_string()),
    };

    let requests: Vec<AlertRequest> = if raw.is_array() {
        serde_json::from_value(raw).unwrap_or_default()
    } else if raw.is_object() {
        if let Ok(single) = serde_json::from_value::<AlertRequest>(raw) {
            vec![single]
        } else {
            vec![]
        }
    } else {
        return (StatusCode::BAD_REQUEST, serde_json::json!({"error": "Expected array or object"}).to_string());
    };

    let mut pending_votes: Vec<VoteTx> = Vec::new();

    {
        let mut stats = s.local_stats.write().await;

        for req in requests {
            let alert_type = req.alert_type.to_uppercase().replace(' ', "_");

            if !VALID_ALERTS.contains(&alert_type.as_str()) {
                continue;
            }

            // In benchmark mode, force-ignore recovery/negative updates so attack bits
            // do not toggle back to 0 before the explicit inter-step reset.
            if benchmark_mode && req.value == 0 {
                continue;
            }

            stats.received += 1;

            // Preserve SAFE_ENVIRONMENT semantics: reset all attack bits to 0.
            if alert_type == "SAFE_ENVIRONMENT" && req.value == 1 {
                for attack in ATTACK_ALERTS {
                    if stats.last_voted.get(*attack).copied() == Some(0) {
                        continue;
                    }
                    stats.processed += 1;
                    stats.last_voted.insert((*attack).to_string(), 0);
                    pending_votes.push(build_vote(&s, attack, 0));
                }
                continue;
            }

            if stats.last_voted.get(&alert_type).copied() == Some(req.value) {
                continue;
            }

            stats.processed += 1;
            stats.last_voted.insert(alert_type.clone(), req.value);
            pending_votes.push(build_vote(&s, &alert_type, req.value));
        }
    }

    for vote in pending_votes {
        s.gossip.submit_vote(vote).await;
    }

    (StatusCode::ACCEPTED,
     serde_json::json!({"success": true, "status": "immediate mode"}).to_string())
}

pub async fn run_batcher(s: ApiState) {
    let _ = s;
    // Batcher intentionally disabled: /alert now submits one VoteTx per effective alert.
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
    }
}

// POST /stress — direct high-throughput mode (same as blockchain_api.js /stress)
async fn handle_stress(
    State(s): State<ApiState>,
    body: axum::body::Bytes,
) -> impl IntoResponse {
    let req: AlertRequest = serde_json::from_slice(&body).unwrap_or_default();
    let alert_type = if req.alert_type.is_empty() {
        "SQL_INJECTION".to_string()
    } else {
        req.alert_type.to_uppercase().replace(' ', "_")
    };

    if !VALID_ALERTS.contains(&alert_type.as_str()) {
        return (StatusCode::BAD_REQUEST,
            serde_json::json!({"error": format!("Unknown alert type: {}", alert_type)}).to_string());
    }

    {
        let mut stats = s.local_stats.write().await;
        stats.received += 1;
        stats.last_voted.insert(alert_type.clone(), req.value);
    }

    let vote = build_vote(&s, &alert_type, req.value);
    // Fire-and-forget for maximum throughput
    tokio::spawn({
        let gossip = s.gossip.clone();
        async move { gossip.submit_vote(vote).await; }
    });

    (StatusCode::ACCEPTED,
     serde_json::json!({"success": true, "status": "Stress alert queued", "type": alert_type}).to_string())
}

// GET /stats
async fn handle_stats(State(s): State<ApiState>) -> impl IntoResponse {
    let stats = s.local_stats.read().await;
    let resp = Stats {
        total_alerts_received: stats.received,
        total_alerts_processed: stats.processed,
    };
    (StatusCode::OK, serde_json::to_string(&resp).unwrap_or_default())
}

// GET /alive — compatible with existing benchmark parser (returns node_id as "account")
async fn handle_alive(State(s): State<ApiState>) -> impl IntoResponse {
    let resp = serde_json::json!({
        "status": "ok",
        "account": s.node_id,   // key kept as "account" for benchmark compat
        "node_id": s.node_id,
        "role": s.role,
    });
    (StatusCode::OK, resp.to_string())
}

// GET /tx_count — reports committed transactions from the ledger.
// Uses Ledger::tx_count() so that both HTTP-ingested votes (via /stress) and
// natively P2P-injected votes (sps-bench) are counted correctly.
async fn handle_tx_count(State(s): State<ApiState>) -> impl IntoResponse {
    let count = s.ledger.read().await.tx_count();
    let resp = TxCount { count };
    (StatusCode::OK, serde_json::to_string(&resp).unwrap_or_default())
}

// POST /admin/populate_map — no-op (map loaded at startup)
async fn handle_populate_map() -> impl IntoResponse {
    (StatusCode::OK, r#"{"status":"action map already populated at startup"}"#)
}

fn build_vote(s: &ApiState, alert_type: &str, value: u64) -> VoteTx {
    let seq = s.seq.fetch_add(1, Ordering::Relaxed);
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    VoteTx {
        id: format!("{}:{}", s.node_id, seq),
        agent_id: s.node_id.clone(),
        parameters: vec![alert_type.to_string()],
        values: vec![value],
        timestamp_ms: ts,
    }
}
