/// Actuator HTTP forwarder — used only by nodes with role = "fullnode".
/// Receives ActionEvents via broadcast channel and POSTs them to the actuator.
use tokio::sync::broadcast;
use crate::types::ActionEvent;

pub async fn run_forwarder(
    mut rx: broadcast::Receiver<ActionEvent>,
    actuator_url: String,
) {
    if actuator_url.is_empty() {
        log::warn!("[FWD] No actuator URL configured — forwarder disabled.");
        return;
    }
    log::info!("[FWD] Actuator forwarder started → {}", actuator_url);

    loop {
        match rx.recv().await {
            Ok(event) => {
                let url = actuator_url.clone();
                let event_clone = event.clone();
                tokio::spawn(async move {
                    forward_action(url, event_clone).await;
                });
            }
            Err(broadcast::error::RecvError::Lagged(n)) => {
                log::warn!("[FWD] Lagged {} events — continuing", n);
            }
            Err(broadcast::error::RecvError::Closed) => {
                log::info!("[FWD] Action channel closed — forwarder stopping.");
                break;
            }
        }
    }
}

async fn forward_action(url: String, event: ActionEvent) {
    let payload = serde_json::json!({
        "action": event.action,
        "selectedAgent": "fullnode0",
        "timestamp": event.timestamp_ms,
        "source": "fullnode0"
    });

    let body = serde_json::to_string(&payload).unwrap_or_default();
    log::info!("[FWD] Forwarding action='{}' to {}", event.action, url);

    // Use tokio's built-in TCP for a lightweight HTTP POST (no reqwest dependency)
    match post_json(&url, &body).await {
        Ok(status) => log::info!("[FWD] Actuator responded HTTP {}", status),
        Err(e) => log::error!("[FWD] Failed to forward: {}", e),
    }

    // Write to log file — mirroring actuator_forwarder.js behaviour
    let log_line = format!(
        "[{}] ACTUATOR_FORWARDER: Action={} \n",
        chrono_now_iso(),
        event.action
    );
    let _ = append_log("/var/log/actuator_forwarder.log", &log_line);
}

/// Minimal HTTP/1.1 POST using raw tokio TCP — avoids reqwest/hyper client deps.
async fn post_json(url: &str, body: &str) -> Result<u16, Box<dyn std::error::Error + Send + Sync>> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpStream;

    // Parse url: http://host:port/path
    let url = url.trim_start_matches("http://");
    let (host_port, path) = if let Some(pos) = url.find('/') {
        (&url[..pos], &url[pos..])
    } else {
        (url, "/")
    };

    let mut stream = TcpStream::connect(host_port).await?;
    let req = format!(
        "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        path, host_port, body.len(), body
    );
    stream.write_all(req.as_bytes()).await?;
    stream.flush().await?;

    let mut resp = Vec::new();
    stream.read_to_end(&mut resp).await?;

    // Parse status line: "HTTP/1.1 200 OK"
    let resp_str = String::from_utf8_lossy(&resp);
    let status: u16 = resp_str
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);
    Ok(status)
}

fn chrono_now_iso() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    // Simple ISO-like format: seconds since epoch
    format!("{}", secs)
}

fn append_log(path: &str, line: &str) -> std::io::Result<()> {
    use std::fs::OpenOptions;
    use std::io::Write;
    let mut f = OpenOptions::new().create(true).append(true).open(path)?;
    f.write_all(line.as_bytes())
}
