//! sps-bench — Native throughput injector for sps-node.
//!
//! Injects `VoteTx` frames **directly into the P2P gossip port** (default 26656)
//! of a running sps-node, completely bypassing the HTTP API. This measures the
//! raw ledger commit throughput without any HTTP, axum, or network-stack overhead.
//!
//! Wire protocol: identical to the sps-chain P2P layer —
//!   4-byte big-endian length  +  JSON-encoded `NetworkMsg::Vote(VoteTx)`
//!
//! Usage:
//!   sps-bench --n <N> [--target <ip:port>] [--api <ip:port>]
//!             [--concurrency <C>] [--step <S>]
//!
//! Output (stdout, **last line only**):
//!   BENCH_STATS:<json>
//!
//! The Python orchestrator (blockchain_benchmark.py) parses this line.

use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::sync::mpsc;

use serde::{Deserialize, Serialize};

// ── Wire types ─────────────────────────────────────────────────────────────
// Mirrored from sps-chain/src/types.rs — kept here to avoid workspace deps.

#[derive(Debug, Clone, Serialize, Deserialize)]
struct VoteTx {
    id: String,
    agent_id: String,
    parameters: Vec<String>,
    values: Vec<u64>,
    timestamp_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "msg_type", content = "payload")]
enum NetworkMsg {
    Vote(VoteTx),
    Ping { from: String },
    Pong { from: String },
}

// ── Output schema ──────────────────────────────────────────────────────────

#[derive(Debug, Serialize)]
struct BenchStats {
    #[serde(rename = "N")]
    n: usize,
    #[serde(rename = "Sent")]
    sent: usize,
    #[serde(rename = "SentTime")]
    sent_time_seconds: f64,
    #[serde(rename = "Transactions")]
    transactions: u64,
    #[serde(rename = "SuccessRate")]
    success_rate: f64,
    #[serde(rename = "TotalTimeSeconds")]
    total_time_seconds: f64,
    #[serde(rename = "TPS")]
    tps: f64,
}

// ── Wire helpers ───────────────────────────────────────────────────────────

/// Writes a single length-prefixed JSON frame — identical to sps-chain's
/// `write_msg()` in p2p.rs.
async fn send_frame(stream: &mut TcpStream, msg: &NetworkMsg) -> std::io::Result<()> {
    let data = serde_json::to_vec(msg)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    let len_bytes = (data.len() as u32).to_be_bytes();
    stream.write_all(&len_bytes).await?;
    stream.write_all(&data).await?;
    Ok(())
}

// ── HTTP helpers ───────────────────────────────────────────────────────────

/// Lightweight HTTP/1.1 GET. Avoids reqwest to keep the binary small.
async fn http_get(addr: &str, path: &str) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let mut stream = TcpStream::connect(addr).await?;
    let req = format!(
        "GET {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
        path, addr
    );
    stream.write_all(req.as_bytes()).await?;
    stream.flush().await?;
    let mut resp = Vec::new();
    stream.read_to_end(&mut resp).await?;
    let s = String::from_utf8_lossy(&resp);
    // Body starts after the first blank line (\r\n\r\n).
    Ok(if let Some(pos) = s.find("\r\n\r\n") {
        s[pos + 4..].to_string()
    } else {
        s.to_string()
    })
}

/// Queries GET /tx_count on the given API address and returns the committed count.
async fn get_tx_count(api_addr: &str) -> u64 {
    match http_get(api_addr, "/tx_count").await {
        Ok(body) => {
            let v: serde_json::Value = serde_json::from_str(&body).unwrap_or_default();
            v.get("count").and_then(|c| c.as_u64()).unwrap_or(0)
        }
        Err(e) => {
            eprintln!("[sps-bench] /tx_count error ({}): {}", api_addr, e);
            0
        }
    }
}

// ── Timestamp ─────────────────────────────────────────────────────────────

#[inline]
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

// ── Statistics ─────────────────────────────────────────────────────────────

// ── Arg parsing ────────────────────────────────────────────────────────────

struct Config {
    n: usize,
    target: String,   // ip:port for P2P connection
    api: String,      // ip:port for HTTP /tx_count
    concurrency: usize,
    queue_depth: usize,
    step: usize,      // benchmark step index (ensures globally unique vote IDs)
}

fn parse_args() -> Config {
    let args: Vec<String> = std::env::args().collect();
    let mut n = 0usize;
    let mut target = "127.0.0.1:26656".to_string();
    let mut api    = "127.0.0.1:3000".to_string();
    let mut concurrency = 8usize;
    let mut queue_depth = 512usize;
    let mut step = 0usize;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--n" | "-n" => {
                i += 1;
                n = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(0);
            }
            "--target" => {
                i += 1;
                target = args.get(i).cloned().unwrap_or(target);
            }
            "--api" => {
                i += 1;
                api = args.get(i).cloned().unwrap_or(api);
            }
            "--concurrency" | "-c" => {
                i += 1;
                concurrency = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(8);
            }
            "--queue-depth" => {
                i += 1;
                queue_depth = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(512);
            }
            "--step" | "-s" => {
                i += 1;
                step = args.get(i).and_then(|v| v.parse().ok()).unwrap_or(0);
            }
            other if !other.starts_with("--") && n == 0 => {
                // Positional first arg treated as N for convenience.
                n = other.parse().unwrap_or(0);
            }
            _ => {}
        }
        i += 1;
    }

    Config { n, target, api, concurrency, queue_depth, step }
}

// ── Main ───────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() {
    let cfg = parse_args();

    if cfg.n == 0 {
        eprintln!(
            "Usage: sps-bench --n <N> [--target <ip:port>] [--api <ip:port>] \
             [--concurrency <C>] [--step <S>]"
        );
        std::process::exit(1);
    }

    eprintln!(
        "[sps-bench] N={} target={} api={} concurrency={} queue_depth={} step={}",
        cfg.n, cfg.target, cfg.api, cfg.concurrency, cfg.queue_depth, cfg.step
    );

    // ── Baseline snapshot ─────────────────────────────────────────────────
    let baseline_tx = get_tx_count(&cfg.api).await;
    eprintln!("[sps-bench] Baseline tx_count={}", baseline_tx);

    // ── Shared state across tasks ──────────────────────────────────────────
    let send_errors = Arc::new(std::sync::atomic::AtomicUsize::new(0));

    let workers = cfg.concurrency.max(1);
    let base_queue_depth = cfg.queue_depth.max(1);
    // Keep producer mostly non-blocking during burst injection by sizing
    // per-worker queue to at least that worker's expected share of N.
    let queue_depth = std::cmp::max(base_queue_depth, (cfg.n / workers) + 1);

    let global_start = Instant::now();
    let mut handles = Vec::with_capacity(workers);
    let mut worker_txs = Vec::with_capacity(workers);

    for worker_id in 0..workers {
        let (tx, mut rx) = mpsc::channel::<(usize, NetworkMsg)>(queue_depth);
        worker_txs.push(tx);

        let target_addr = cfg.target.clone();
        let errs = send_errors.clone();

        handles.push(tokio::spawn(async move {
            let mut stream: Option<TcpStream> = None;
            let mut local_errors = 0usize;
            let mut local_sent = 0usize;

            while let Some((idx, msg)) = rx.recv().await {
                let mut sent = false;

                for _attempt in 0..3 {
                    if stream.is_none() {
                        match TcpStream::connect(&target_addr).await {
                            Ok(s) => {
                                let _ = s.set_nodelay(true);
                                stream = Some(s);
                            }
                            Err(e) => {
                                eprintln!("[sps-bench:{}] Connect failed: {}", worker_id, e);
                                tokio::time::sleep(Duration::from_millis(25)).await;
                                continue;
                            }
                        }
                    }

                    let write_res = if let Some(s) = stream.as_mut() {
                        send_frame(s, &msg).await
                    } else {
                        Err(std::io::Error::new(std::io::ErrorKind::Other, "stream not available"))
                    };

                    if write_res.is_ok() {
                        local_sent += 1;
                        sent = true;
                        break;
                    }

                    stream = None;
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }

                if !sent {
                    local_errors += 1;
                    eprintln!("[sps-bench:{}] Dropped vote idx={} after retries", worker_id, idx);
                }
            }

            if let Some(mut s) = stream {
                let _ = s.flush().await;
            }

            errs.fetch_add(local_errors, std::sync::atomic::Ordering::Relaxed);

            eprintln!(
                "[sps-bench:{}] Queue drained — sent={} errors={}",
                worker_id,
                local_sent,
                local_errors
            );
        }));
    }

    // Feed votes into bounded worker queues (backpressure instead of burst spikes).
    for idx in 0..cfg.n {
        let worker_id = idx % workers;
        let vote = VoteTx {
            id: format!("sps-bench:s{}:w{}:{}", cfg.step, worker_id, idx),
            agent_id: format!("sps-bench-q{}-s{}", worker_id, cfg.step),
            parameters: vec!["SQL_INJECTION".to_string()],
            values: vec![1],
            timestamp_ms: now_ms(),
        };
        let msg = NetworkMsg::Vote(vote);
        if worker_txs[worker_id].send((idx, msg)).await.is_err() {
            send_errors.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        }
    }
    drop(worker_txs);

    // Wait for all injection tasks to complete.
    for h in handles {
        let _ = h.await;
    }

    let send_elapsed = global_start.elapsed();
    eprintln!(
        "[sps-bench] All {} frames dispatched in {:.3}s. Polling /tx_count...",
        cfg.n,
        send_elapsed.as_secs_f64()
    );

    // ── Poll /tx_count until committed count genuinely settles ─────────────
    // Track monotonic best count and wait for sustained no-progress window.
    let mut best_tx = baseline_tx;
    let poll_started = Instant::now();
    let mut last_progress_at = poll_started;
    let mut saw_progress = false;

    let adaptive_deadline_s = ((cfg.n as u64) / 3_000).clamp(90, 600);
    let poll_deadline = poll_started + Duration::from_secs(adaptive_deadline_s);
    let min_observe_before_settle = Duration::from_secs(8);
    let settle_no_progress_window = Duration::from_secs(12);

    let final_tx = loop {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let current = get_tx_count(&cfg.api).await;
        if current > best_tx {
            best_tx = current;
            saw_progress = true;
            last_progress_at = Instant::now();
        }

        let committed = best_tx.saturating_sub(baseline_tx);
        eprintln!("[sps-bench] tx_count={} best={} committed={}/{}", current, best_tx, committed, cfg.n);

        if committed >= cfg.n as u64 {
            break best_tx;
        }

        let now = Instant::now();
        let observed_for = now.saturating_duration_since(poll_started);
        let no_progress_for = now.saturating_duration_since(last_progress_at);

        if saw_progress
            && observed_for >= min_observe_before_settle
            && no_progress_for >= settle_no_progress_window
        {
            eprintln!(
                "[sps-bench] Settled after {:.1}s with no progress for {:.1}s.",
                observed_for.as_secs_f64(),
                no_progress_for.as_secs_f64()
            );
            break best_tx;
        }

        if now >= poll_deadline {
            eprintln!(
                "[sps-bench] Deadline ({}s) reached. committed={}/{}",
                adaptive_deadline_s,
                committed,
                cfg.n
            );
            break best_tx;
        }
    };

    let total_time = global_start.elapsed().as_secs_f64();
    let committed  = final_tx.saturating_sub(baseline_tx);
    let _n_errors   = send_errors.load(std::sync::atomic::Ordering::Relaxed);

    let success_rate = if cfg.n > 0 {
        committed as f64 / cfg.n as f64 * 100.0
    } else {
        100.0
    };
    let tps = if total_time > 0.0 { committed as f64 / total_time } else { 0.0 };

    let stats = BenchStats {
        n:                   cfg.n,
        sent:                cfg.n,
        sent_time_seconds:   send_elapsed.as_secs_f64(),
        transactions:        committed,
        success_rate,
        total_time_seconds:  total_time,
        tps
    };

    // ── Final output (parsed by Python orchestrator) ───────────────────────
    println!("BENCH_STATS:{}", serde_json::to_string(&stats).unwrap());
}
