/// TCP gossip layer for vote propagation.
/// Uses epidemic (flood-fill) broadcast with SHA256-based deduplication.
/// Protocol: 4-byte big-endian length prefix followed by JSON payload.
use std::collections::HashSet;
use std::sync::Arc;
use tokio::sync::{Mutex, RwLock, broadcast, mpsc};
use tokio::net::{TcpListener, TcpStream};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use crate::types::{NetworkMsg, VoteTx, ActionEvent};
use crate::ledger::Ledger;

const MAX_MSG_SIZE: usize = 10 * 1024 * 1024; // 10 MB

/// Clone-able handle to shared gossip state.
#[derive(Clone)]
pub struct GossipHandle {
    pub node_id: String,
    seen: Arc<Mutex<HashSet<String>>>,
    pub ledger: Arc<RwLock<Ledger>>,
    pub action_tx: broadcast::Sender<ActionEvent>,
    /// Senders to each connected peer (mpsc channel per connection)
    peer_tx: Arc<Mutex<Vec<mpsc::UnboundedSender<VoteTx>>>>,
}

impl GossipHandle {
    pub fn new(
        node_id: String,
        ledger: Arc<RwLock<Ledger>>,
        action_tx: broadcast::Sender<ActionEvent>,
    ) -> Self {
        Self {
            node_id,
            seen: Arc::new(Mutex::new(HashSet::new())),
            ledger,
            action_tx,
            peer_tx: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// Process a vote (dedup → apply to ledger → broadcast to peers).
    /// Returns true if vote was new.
    pub async fn receive_vote(&self, vote: VoteTx) -> bool {
        // Deduplication
        {
            let mut seen = self.seen.lock().await;
            if seen.contains(&vote.id) {
                return false;
            }
            seen.insert(vote.id.clone());
        }

        // Apply to ledger
        let action_opt = {
            let mut ledger = self.ledger.write().await;
            ledger.propose_new_values(&vote.agent_id, &vote.parameters, &vote.values)
        };

        // Emit action event if triggered
        if let Some(event) = action_opt {
            let _ = self.action_tx.send(event);
        }

        // Broadcast to all peers (removes dead senders automatically)
        {
            let mut senders = self.peer_tx.lock().await;
            senders.retain(|s| s.send(vote.clone()).is_ok());
        }

        true
    }

    /// Submit a locally-generated vote (from the API layer).
    pub async fn submit_vote(&self, vote: VoteTx) {
        self.receive_vote(vote).await;
    }

    /// Start the P2P listener (accepts inbound connections from peers).
    pub async fn start_listener(self, addr: String) {
        let listener = match TcpListener::bind(&addr).await {
            Ok(l) => l,
            Err(e) => { log::error!("[P2P] Cannot bind {}: {}", addr, e); return; }
        };
        log::info!("[P2P] Listening on {}", addr);
        loop {
            match listener.accept().await {
                Ok((stream, peer)) => {
                    log::info!("[P2P] Connection from {}", peer);
                    let handle = self.clone();
                    tokio::spawn(async move { handle.run_stream(stream).await; });
                }
                Err(e) => {
                    log::warn!("[P2P] Accept error: {}", e);
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                }
            }
        }
    }

    /// Connect to all configured peer addresses with automatic retry.
    pub async fn connect_to_peers(self, addrs: Vec<String>) {
        for addr in addrs {
            let handle = self.clone();
            let a = addr.clone();
            tokio::spawn(async move {
                handle.connect_with_retry(a).await;
            });
        }
    }

    async fn connect_with_retry(&self, addr: String) {
        let mut delay_ms = 500u64;
        loop {
            match TcpStream::connect(&addr).await {
                Ok(stream) => {
                    log::info!("[P2P] Connected to peer {}", addr);
                    delay_ms = 500;
                    self.run_stream(stream).await;
                    log::warn!("[P2P] Lost peer {}, reconnecting in {}ms", addr, delay_ms);
                }
                Err(e) => {
                    log::debug!("[P2P] Cannot reach {}: {}. Retry in {}ms", addr, e, delay_ms);
                }
            }
            tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
            delay_ms = (delay_ms * 2).min(10_000);
        }
    }

    /// Handle a single TCP connection (bidirectional).
    async fn run_stream(&self, stream: TcpStream) {
        let (mut reader, mut writer) = stream.into_split();

        // Create per-connection sender/receiver for outbound votes
        let (tx, mut rx) = mpsc::unbounded_channel::<VoteTx>();
        {
            let mut senders = self.peer_tx.lock().await;
            senders.push(tx);
        }

        // Writer task: drain the mpsc channel → TCP
        let write_task = tokio::spawn(async move {
            while let Some(vote) = rx.recv().await {
                let msg = NetworkMsg::Vote(vote);
                if write_msg(&mut writer, &msg).await.is_err() {
                    break;
                }
            }
        });

        // Reader task: TCP → process votes
        let handle = self.clone();
        let read_task = tokio::spawn(async move {
            loop {
                match read_msg(&mut reader).await {
                    Ok(NetworkMsg::Vote(vote)) => { handle.receive_vote(vote).await; }
                    Ok(NetworkMsg::Ping { from }) => {
                        log::trace!("[P2P] Ping from {}", from);
                    }
                    Ok(NetworkMsg::Pong { .. }) => {}
                    Err(e) => {
                        log::debug!("[P2P] Read error: {}", e);
                        break;
                    }
                }
            }
        });

        // Wait for either half to finish, then cancel both
        tokio::select! {
            _ = write_task => {}
            _ = read_task => {}
        }
    }
}

// --------------- Framing helpers ---------------

async fn write_msg<W: AsyncWriteExt + Unpin>(w: &mut W, msg: &NetworkMsg) -> std::io::Result<()> {
    let data = serde_json::to_vec(msg)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    let len = data.len() as u32;
    w.write_all(&len.to_be_bytes()).await?;
    w.write_all(&data).await?;
    w.flush().await
}

async fn read_msg<R: AsyncReadExt + Unpin>(r: &mut R) -> std::io::Result<NetworkMsg> {
    let mut len_buf = [0u8; 4];
    r.read_exact(&mut len_buf).await?;
    let len = u32::from_be_bytes(len_buf) as usize;
    if len > MAX_MSG_SIZE {
        return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "msg too large"));
    }
    let mut buf = vec![0u8; len];
    r.read_exact(&mut buf).await?;
    serde_json::from_slice(&buf)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
}
