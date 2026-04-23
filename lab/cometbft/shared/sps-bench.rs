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
    #[serde(rename = "Transactions")]
    transactions: u64,
    #[serde(rename = "SuccessRate")]
    success_rate: f64,
    #[serde(rename = "TotalTimeSeconds")]
    total_time_seconds: f64,
    #[serde(rename = "TPS")]
    tps: f64,
    #[serde(rename = "SendErrors")]
    send_errors: usize,
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

fn percentile(sorted: &[u64], pct: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((sorted.len() as f64 * pct / 100.0).ceil() as usize).saturating_sub(1);
    sorted[idx.min(sorted.len() - 1)] as f64
}

fn mean(xs: &[u64]) -> f64 {
    if xs.is_empty() {
        return 0.0;
    }
    xs.iter().sum::<u64>() as f64 / xs.len() as f64
}

// ── Arg parsing ────────────────────────────────────────────────────────────

struct Config {
    n: usize,
    target: String,   // ip:port for P2P connection
    api: String,      // ip:port for HTTP /tx_count
    concurrency: usize,
    step: usize,      // benchmark step index (ensures globally unique vote IDs)
}

fn parse_args() -> Config {
    let args: Vec<String> = std::env::args().collect();
    let mut n = 0usize;
    let mut target = "127.0.0.1:26656".to_string();
    let mut api    = "127.0.0.1:3000".to_string();
    let mut concurrency = 8usize;
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

    Config { n, target, api, concurrency, step }
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
        "[sps-bench] N={} target={} api={} concurrency={} step={}",
        cfg.n, cfg.target, cfg.api, cfg.concurrency, cfg.step
    );

    // ── Baseline snapshot ─────────────────────────────────────────────────
    let baseline_tx = get_tx_count(&cfg.api).await;
    eprintln!("[sps-bench] Baseline tx_count={}", baseline_tx);

    // ── Shared state across tasks ──────────────────────────────────────────
    let send_errors = Arc::new(std::sync::atomic::AtomicUsize::new(0));

    // Divide N votes evenly across `concurrency` tasks.
    let per_task = (cfg.n + cfg.concurrency - 1) / cfg.concurrency;

    let global_start = Instant::now();
    let mut handles = Vec::with_capacity(cfg.concurrency);

    for task_id in 0..cfg.concurrency {
        let start_idx = task_id * per_task;
        let end_idx   = ((task_id + 1) * per_task).min(cfg.n);
        if start_idx >= end_idx {
            break;
        }

        let target_addr  = cfg.target.clone();
        let agent_id     = format!("sps-bench-t{}-s{}", task_id, cfg.step);
        let step         = cfg.step;
        let errs         = send_errors.clone();

        handles.push(tokio::spawn(async move {
            // Each task holds ONE persistent TCP connection to the P2P port.
            let mut stream = match TcpStream::connect(&target_addr).await {
                Ok(s) => {
                    // Disable Nagle's algorithm: we handle our own framing.
                    let _ = s.set_nodelay(true);
                    s
                }
                Err(e) => {
                    eprintln!("[sps-bench:{}] Cannot connect to {}: {}", task_id, target_addr, e);
                    errs.fetch_add(end_idx - start_idx, std::sync::atomic::Ordering::Relaxed);
                    return;
                }
            };

            eprintln!(
                "[sps-bench:{}] Connected — injecting votes {}..{}",
                task_id, start_idx, end_idx - 1
            );

            let mut local_errors = 0usize;

            for idx in start_idx..end_idx {
                // Vote ID: globally unique across all benchmark runs and steps.
                // Format ensures no collision with live sps-node IDs (which are "node_id:seq").
                let vote_id = format!("sps-bench:s{}:t{}:{}", step, task_id, idx);
                let ts      = now_ms();

                let vote = VoteTx {
                    id:         vote_id,
                    agent_id:   agent_id.clone(),
                    parameters: vec!["SQL_INJECTION".to_string()],
                    values:     vec![1],
                    timestamp_ms: ts,
                };
                let msg = NetworkMsg::Vote(vote);

                match send_frame(&mut stream, &msg).await {
                    Ok(_) => {}
                    Err(e) => {
                        eprintln!("[sps-bench:{}] Frame error at idx {}: {}", task_id, idx, e);
                        local_errors += 1;
                        // Attempt to reconnect and retry this vote once.
                        match TcpStream::connect(&target_addr).await {
                            Ok(s) => {
                                let _ = s.set_nodelay(true);
                                stream = s;
                                // Retry the same frame on the fresh connection.
                                let vote2 = VoteTx {
                                    id:         format!("sps-bench:s{}:t{}:{}r", step, task_id, idx),
                                    agent_id:   agent_id.clone(),
                                    parameters: vec!["SQL_INJECTION".to_string()],
                                    values:     vec![1],
                                    timestamp_ms: now_ms(),
                                };
                                let msg2 = NetworkMsg::Vote(vote2);
                                if send_frame(&mut stream, &msg2).await.is_ok() {
                                    local_errors -= 1; // recovered
                                }
                            }
                            Err(_) => {
                                // Cannot reconnect — remaining votes on this task will fail.
                                errs.fetch_add(end_idx - idx - 1, std::sync::atomic::Ordering::Relaxed);
                                break;
                            }
                        }
                    }
                }
            }

            // Flush the OS send buffer before we declare "sent".
            let _ = stream.flush().await;

            errs.fetch_add(local_errors, std::sync::atomic::Ordering::Relaxed);

            eprintln!(
                "[sps-bench:{}] Done — {} errors",
                task_id, local_errors
            );
        }));
    }

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

    // ── Poll /tx_count until committed count stabilises ────────────────────
    // The P2P receive_vote() path is async; the ledger RwLock may serialise
    // processing of votes after the last TCP flush. We poll until stable.
    let mut final_tx    = baseline_tx;
    let mut last_count  = baseline_tx;
    let mut stable_ticks = 0u32;
    let poll_deadline   = Instant::now() + Duration::from_secs(60);

    loop {
        tokio::time::sleep(Duration::from_millis(200)).await;
        let current  = get_tx_count(&cfg.api).await;
        let committed = current.saturating_sub(baseline_tx);

        eprintln!("[sps-bench] tx_count={} committed={}/{}", current, committed, cfg.n);

        if committed >= cfg.n as u64 {
            final_tx = current;
            break;
        }
        if current == last_count {
            stable_ticks += 1;
            if stable_ticks >= 10 {
                // Count has been stable for 2 seconds — ledger has caught up.
                final_tx = current;
                break;
            }
        } else {
            stable_ticks = 0;
        }
        last_count = current;

        if Instant::now() >= poll_deadline {
            eprintln!("[sps-bench] Deadline reached. committed={}/{}", committed, cfg.n);
            final_tx = current;
            break;
        }
    }

    let total_time = global_start.elapsed().as_secs_f64();
    let committed  = final_tx.saturating_sub(baseline_tx);
    let n_errors   = send_errors.load(std::sync::atomic::Ordering::Relaxed);

    let success_rate = if cfg.n > 0 {
        committed as f64 / cfg.n as f64 * 100.0
    } else {
        100.0
    };
    let tps = if total_time > 0.0 { committed as f64 / total_time } else { 0.0 };

    let stats = BenchStats {
        n:                   cfg.n,
        sent:                cfg.n,
        transactions:        committed,
        success_rate,
        total_time_seconds:  total_time,
        tps,
        send_errors:         n_errors,
    };

    // ── Final output (parsed by Python orchestrator) ───────────────────────
    println!("BENCH_STATS:{}", serde_json::to_string(&stats).unwrap());
}
