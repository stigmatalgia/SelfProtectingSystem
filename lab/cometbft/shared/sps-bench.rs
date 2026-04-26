//! sps-bench — Native HTTP throughput injector for sps-node.
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize)]
struct BenchStats {
    #[serde(rename = "N")]
    n: usize,
    #[serde(rename = "Sent")]
    sent: usize,
    #[serde(rename = "Transactions")]
    transactions: u64,
    #[serde(rename = "SuccessRate")]
    success_rate: f64,
    #[serde(rename = "SentTime")]            // Added to track true injection time
    sent_time: f64, 
    #[serde(rename = "TotalTimeSeconds")]
    total_time_seconds: f64,
    #[serde(rename = "TPS")]
    tps: f64,
    #[serde(rename = "SendErrors")]
    send_errors: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct AlertPayload {
    ids: String,
    message: String,
    #[serde(rename = "type")]
    alert_type: String,
    value: u64,
    timestamp: String,
}

async fn http_get(addr: &str, path: &str) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let mut stream = TcpStream::connect(addr).await?;
    let req = format!("GET {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n", path, addr);
    stream.write_all(req.as_bytes()).await?;
    let mut resp = Vec::new();
    stream.read_to_end(&mut resp).await?;
    let s = String::from_utf8_lossy(&resp);
    Ok(if let Some(pos) = s.find("\r\n\r\n") { s[pos + 4..].to_string() } else { s.to_string() })
}

async fn get_tx_count(api_addr: &str) -> u64 {
    match http_get(api_addr, "/tx_count").await {
        Ok(body) => {
            let v: serde_json::Value = serde_json::from_str(&body).unwrap_or_default();
            v.get("count").and_then(|c| c.as_u64()).unwrap_or(0)
        }
        Err(_) => 0,
    }
}

#[inline]
fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64
}

struct Config { n: usize, target: String, api: String, concurrency: usize, step: usize }

fn parse_args() -> Config {
    let args: Vec<String> = std::env::args().collect();
    let mut n = 0usize;
    let mut target = "127.0.0.1:3000".to_string(); 
    let mut api    = "127.0.0.1:3000".to_string();
    let mut concurrency = 8usize;
    let mut step = 0usize;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--n" | "-n" => { i += 1; n = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(0); }
            "--target" => { i += 1; target = args.get(i).cloned().unwrap_or(target); }
            "--api" => { i += 1; api = args.get(i).cloned().unwrap_or(api); }
            "--concurrency" | "-c" => { i += 1; concurrency = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(8); }
            "--step" | "-s" => { i += 1; step = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(0); }
            _ => {}
        }
        i += 1;
    }
    Config { n, target, api, concurrency, step }
}

#[tokio::main]
async fn main() {
    let cfg = parse_args();
    if cfg.n == 0 { std::process::exit(1); }

    let baseline_tx = get_tx_count(&cfg.api).await;
    let send_errors = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let per_task = (cfg.n + cfg.concurrency - 1) / cfg.concurrency;

    let global_start = Instant::now();
    let mut handles = Vec::with_capacity(cfg.concurrency);

    for task_id in 0..cfg.concurrency {
        let start_idx = task_id * per_task;
        let end_idx   = ((task_id + 1) * per_task).min(cfg.n);
        if start_idx >= end_idx { break; }

        let target_addr  = cfg.target.clone();
        let agent_id     = format!("sps-bench-t{}-s{}", task_id, cfg.step);
        let errs         = send_errors.clone();

        handles.push(tokio::spawn(async move {
            let mut local_errors = 0usize;
            match TcpStream::connect(&target_addr).await {
                Ok(stream) => {
                    let (mut rh, mut wh) = stream.into_split();
                    let reader_handle = tokio::spawn(async move {
                        let mut buf = [0u8; 8192];
                        while let Ok(n) = rh.read(&mut buf).await { if n == 0 { break; } }
                    });

                    for idx in start_idx..end_idx {
                        let is_last = idx == end_idx - 1;
                        let conn_hdr = if is_last { "close" } else { "keep-alive" };
                        let vote_id = format!("sps-bench:s{}:t{}:{}", cfg.step, task_id, idx);
                        let payload = vec![AlertPayload {
                            ids: agent_id.clone(),
                            message: vote_id,
                            alert_type: "SQL_INJECTION".to_string(),
                            value: 1,
                            timestamp: now_ms().to_string(),
                        }];

                        let body = serde_json::to_string(&payload).unwrap();
                        let req = format!(
                            "POST /alert HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: {}\r\n\r\n{}",
                            target_addr, body.len(), conn_hdr, body
                        );

                        if wh.write_all(req.as_bytes()).await.is_err() {
                            local_errors += 1;
                            break;
                        }
                    }
                    let _ = wh.shutdown().await;
                    let _ = tokio::time::timeout(Duration::from_secs(60), reader_handle).await;
                }
                Err(_) => { local_errors += end_idx - start_idx; }
            }
            errs.fetch_add(local_errors, std::sync::atomic::Ordering::Relaxed);
        }));
    }

    for h in handles { let _ = h.await; }
    let actual_send_duration = global_start.elapsed().as_secs_f64();
    eprintln!("[sps-bench] Burst dispatched to mempool in {:.3}s.", actual_send_duration);
    
    let mut final_tx = baseline_tx;
    let mut last_count = baseline_tx;
    let mut stable_ticks = 0u32;
    let poll_deadline = Instant::now() + Duration::from_secs(300); // Increased deadline

    loop {
        tokio::time::sleep(Duration::from_millis(200)).await;
        let current = get_tx_count(&cfg.api).await;
        let committed = current.saturating_sub(baseline_tx);

        if committed >= cfg.n as u64 {
            final_tx = current;
            break;
        }
        if current == last_count {
            stable_ticks += 1;
            if stable_ticks >= 15 {
                final_tx = current;
                break;
            }
        } else {
            stable_ticks = 0;
        }
        last_count = current;

        if Instant::now() >= poll_deadline {
            final_tx = current;
            break;
        }
    }

    let total_time = global_start.elapsed().as_secs_f64();
    let committed  = final_tx.saturating_sub(baseline_tx);
    let n_errors   = send_errors.load(std::sync::atomic::Ordering::Relaxed);

    let stats = BenchStats {
        n: cfg.n,
        sent: cfg.n,
        transactions: committed,
        success_rate: if cfg.n > 0 { committed as f64 / cfg.n as f64 * 100.0 } else { 100.0 },
        sent_time: actual_send_duration, // Correctly populating the new field
        total_time_seconds: total_time,
        tps: if total_time > 0.0 { committed as f64 / total_time } else { 0.0 },
        send_errors: n_errors,
    };

    println!("BENCH_STATS:{}", serde_json::to_string(&stats).unwrap());
}