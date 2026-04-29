//! sps-bench — raw CometBFT WebSocket injector for pure mempool throughput tests.
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{connect_async, MaybeTlsStream, WebSocketStream};

#[derive(Debug, Serialize)]
struct BenchStats {
    #[serde(rename = "N")]
    n: usize,
    #[serde(rename = "Sent")]
    sent: usize,
    #[serde(rename = "SendErrors")]
    send_errors: usize,
    #[serde(rename = "PrecomputeSeconds")]
    precompute_seconds: f64,
    #[serde(rename = "SentTime")]
    sent_time: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct VoteTx {
    pub id: u64,
    pub agent_id: u32,
    pub param_mask: u32,
    pub values: [u64; 4],
    pub timestamp_ms: u64,
}

struct Config {
    n: usize,
    concurrency: usize,
    step: usize,
    sleep_ms: u64,
    targets: Vec<String>,
}

fn parse_args() -> Config {
    let args: Vec<String> = std::env::args().collect();
    let mut n = 0usize;
    let mut concurrency = 64usize;
    let mut step = 0usize;
    let mut sleep_ms = 0u64;
    let mut targets =
        "validator0:26657,validator1:26657,validator2:26657".to_string();

    let mut i = 1usize;
    while i < args.len() {
        match args[i].as_str() {
            "--n" | "-n" => {
                i += 1;
                n = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(0);
            }
            "--concurrency" | "-c" => {
                i += 1;
                concurrency = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(64);
            }
            "--step" | "-s" => {
                i += 1;
                step = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(0);
            }
            "--sleep-ms" => {
                i += 1;
                sleep_ms = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(0);
            }
            "--targets" => {
                i += 1;
                targets = args.get(i).cloned().unwrap_or(targets);
            }
            _ => {}
        }
        i += 1;
    }

    let parsed_targets: Vec<String> = targets
        .split(',')
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .map(ToOwned::to_owned)
        .collect();

    Config {
        n,
        concurrency,
        step,
        sleep_ms,
        targets: parsed_targets,
    }
}

#[inline]
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn build_rpc_frames(n: usize, step: usize) -> Vec<String> {
    let mut frames = Vec::with_capacity(n);
    for i in 0..n {
        let vote = VoteTx {
            id: (step as u64) * 1_000_000 + (i as u64),
            agent_id: (i % 3) as u32,
            param_mask: 0b0001, // SQL_INJECTION bit
            values: [1, 0, 0, 0],
            timestamp_ms: now_ms(),
        };

        let tx_bin_bytes = bincode::serialize(&vote).expect("VoteTx bincode serialization failed");
        let tx_b64 = BASE64_STANDARD.encode(tx_bin_bytes);
        let rpc = serde_json::json!({
            "jsonrpc": "2.0",
            "id": format!("bench-{}", i),
            "method": "broadcast_tx_async",
            "params": { "tx": tx_b64 }
        });
        frames.push(rpc.to_string());
    }
    frames
}

async fn open_ws(target: &str) -> Result<WebSocketStream<MaybeTlsStream<TcpStream>>, String> {
    let url = format!("ws://{}/websocket", target);
    match connect_async(&url).await {
        Ok((ws, _)) => Ok(ws),
        Err(e) => Err(format!("websocket connect failed for {}: {}", target, e)),
    }
}

#[tokio::main]
async fn main() {
    let cfg = parse_args();
    if cfg.n == 0 || cfg.targets.is_empty() {
        std::process::exit(1);
    }

    let precompute_start = Instant::now();
    let frames = Arc::new(build_rpc_frames(cfg.n, cfg.step));
    let precompute_seconds = precompute_start.elapsed().as_secs_f64();

    let workers = cfg.concurrency.max(1).min(cfg.n.max(1));
    let send_errors = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let sent_ok = Arc::new(std::sync::atomic::AtomicUsize::new(0));

    let send_start = Instant::now();
    let mut handles = Vec::with_capacity(workers);

    for worker_id in 0..workers {
        let target = cfg.targets[worker_id % cfg.targets.len()].clone();
        let all_frames = frames.clone();
        let errs = send_errors.clone();
        let sent = sent_ok.clone();
        let sleep_ms = cfg.sleep_ms;

handles.push(tokio::spawn(async move {
            let ws = match open_ws(&target).await {
                Ok(ws) => ws,
                Err(_) => {
                    let failed = (worker_id..all_frames.len()).step_by(workers).count();
                    errs.fetch_add(failed, std::sync::atomic::Ordering::Relaxed);
                    return;
                }
            };

            if let MaybeTlsStream::Plain(tcp) = ws.get_ref() {
                let _ = tcp.set_nodelay(true);
            }

            let (mut write, mut read) = ws.split();

            tokio::spawn(async move {
                while let Some(_) = read.next().await {
                }
            });

            for idx in (worker_id..all_frames.len()).step_by(workers) {
                if write
                    .send(Message::Text(all_frames[idx].clone().into()))
                    .await
                    .is_ok()
                {
                    sent.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                } else {
                    errs.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                }
                if sleep_ms > 0 {
                    tokio::time::sleep(std::time::Duration::from_millis(sleep_ms)).await;
                }
            }

            let _ = write.send(Message::Close(None)).await;
        }));
    }

    for h in handles {
        let _ = h.await;
    }
    let sent_time = send_start.elapsed().as_secs_f64();
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    let stats = BenchStats {
        n: cfg.n,
        sent: sent_ok.load(std::sync::atomic::Ordering::Relaxed),
        send_errors: send_errors.load(std::sync::atomic::Ordering::Relaxed),
        precompute_seconds,
        sent_time,
    };

    println!("BENCH_STATS:{}", serde_json::to_string(&stats).unwrap());
}
