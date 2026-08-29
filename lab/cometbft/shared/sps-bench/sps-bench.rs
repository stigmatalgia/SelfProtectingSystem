//! sps-bench — CometBFT JSON-RPC injector for end-to-end throughput tests.
//!
//! Each worker POSTs one tx per request to the JSON-RPC root endpoint `/`
//! (`broadcast_tx_sync` or `broadcast_tx_async`), spreading the burst over the
//! configured target validators. The async mode returns immediately and lets
//! the mempool admit txs in the background; the sync mode gives deterministic
//! backpressure (each worker waits for the CheckTx response before the next
//! request).
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine;
use serde::{Deserialize, Serialize};

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
    mode: Mode,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Sync,
    Async,
}

impl Mode {
    fn method(self) -> &'static str {
        match self {
            Mode::Sync => "broadcast_tx_sync",
            Mode::Async => "broadcast_tx_async",
        }
    }
}

fn parse_args() -> Config {
    let args: Vec<String> = std::env::args().collect();
    let mut n = 0usize;
    let mut concurrency = 256usize;
    let mut step = 0usize;
    let mut sleep_ms = 0u64;
    let mut targets = "validator0:26657,validator1:26657,validator2:26657".to_string();
    let mut mode = Mode::Async;

    let mut i = 1usize;
    while i < args.len() {
        match args[i].as_str() {
            "--n" | "-n" => { i += 1; n = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(0); }
            "--concurrency" | "-c" => { i += 1; concurrency = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(256); }
            "--step" | "-s" => { i += 1; step = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(0); }
            "--sleep-ms" => { i += 1; sleep_ms = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(0); }
            "--targets" => { i += 1; targets = args.get(i).cloned().unwrap_or(targets); }
            "--mode" | "-m" => {
                i += 1;
                mode = match args.get(i).map(|v| v.as_str()) {
                    Some("sync") => Mode::Sync,
                    _ => Mode::Async,
                };
            }
            _ => {}
        }
        i += 1;
    }

    let parsed_targets: Vec<String> = targets.split(',').map(str::trim).filter(|t| !t.is_empty()).map(ToOwned::to_owned).collect();
    Config { n, concurrency, step, sleep_ms, targets: parsed_targets, mode }
}

#[inline]
fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64
}

fn build_rpc_frames(n: usize, step: usize, mode: Mode) -> Vec<String> {
    let ms = now_ms();
    let base = (ms << 21) | (((step as u64) & 0xF) << 17);
    assert!(n <= 0x1FFFF, "burst larger than 131072 txs exceeds id space");

    let mut frames = Vec::with_capacity(n);
    for i in 0..n {
        let vote = VoteTx {
            id: base + (i as u64),
            agent_id: (i % 3) as u32,
            param_mask: 0b0001,
            values: [1, 0, 0, 0],
            timestamp_ms: now_ms(),
        };
        let tx_bin_bytes = bincode::serialize(&vote).expect("VoteTx bincode serialization failed");
        let tx_b64 = BASE64_STANDARD.encode(tx_bin_bytes);
        let rpc = serde_json::json!({
            "jsonrpc": "2.0",
            "id": format!("bench-{}", i),
            "method": mode.method(),
            "params": { "tx": tx_b64 }
        });
        frames.push(rpc.to_string());
    }
    frames
}

#[tokio::main]
async fn main() {
    let cfg = parse_args();
    if cfg.n == 0 || cfg.targets.is_empty() {
        std::process::exit(1);
    }

    let precompute_start = Instant::now();
    let frames = Arc::new(build_rpc_frames(cfg.n, cfg.step, cfg.mode));
    let precompute_seconds = precompute_start.elapsed().as_secs_f64();

    let workers = cfg.concurrency.max(1).min(cfg.n.max(1));
    let send_errors = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let sent_ok = Arc::new(std::sync::atomic::AtomicUsize::new(0));

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .pool_idle_timeout(std::time::Duration::from_secs(30))
        .build()
        .expect("failed to build HTTP client");
    let client = Arc::new(client);

    let send_start = Instant::now();
    let mut handles = Vec::with_capacity(workers);

    for worker_id in 0..workers {
        let client = client.clone();
        let all_frames = frames.clone();
        let errs = send_errors.clone();
        let sent = sent_ok.clone();
        let sleep_ms = cfg.sleep_ms;
        let target = cfg.targets[worker_id % cfg.targets.len()].clone();
        // CometBFT JSON-RPC: POST to the root path `/` with the method in the
        // JSON body. Posting to `/<method>` hits the legacy URI handler, which
        // interprets `params` positionally and silently decodes `tx` as empty.
        let url = format!("http://{}/", target);

        handles.push(tokio::spawn(async move {
            for idx in (worker_id..all_frames.len()).step_by(workers) {
                let payload = all_frames[idx].clone();
                let resp = client.post(&url)
                    .header("Content-Type", "application/json")
                    .body(payload)
                    .send()
                    .await;
                match resp {
                    Ok(r) => {
                        let status = r.status();
                        if status.is_success() {
                            // Even on CheckTx failure (non-zero code), the HTTP
                            // response is 200 — we count delivery, not success.
                            sent.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        } else {
                            let n = errs.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                            if n < 5 {
                                let body = r.text().await.unwrap_or_default();
                                eprintln!("[sps-bench] HTTP {} body({}): {}", status, body.len(), &body[..body.len().min(200)]);
                            }
                        }
                    }
                    Err(e) => {
                        let n = errs.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        if n < 3 {
                            eprintln!("[sps-bench] send error: {:?}", e);
                        }
                    }
                }
                if sleep_ms > 0 {
                    tokio::time::sleep(std::time::Duration::from_millis(sleep_ms)).await;
                }
            }
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
