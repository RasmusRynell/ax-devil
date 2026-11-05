//! An in-process MQTT 3.1.1 broker on 127.0.0.1, built on rumqttc's own packet codec: one login, QoS 0 delivery to
//! subscribers, and knobs to refuse a subscription or drop every connection.

use std::net::SocketAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use bytes::BytesMut;
use rumqttc::{
    ConnAck, ConnectReturnCode, Error, Packet, Publish, QoS, SubAck, SubscribeReasonCode,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::mpsc;
use tokio::task::{JoinHandle, JoinSet};

pub const USER: &str = "broker-user";
pub const PASSWORD: &str = "broker-pass-9137";
const MAX: usize = 8 << 20;

/// How the fake behaves.
#[derive(Clone, Default)]
pub struct Behaviour {
    /// Only [`USER`] with [`PASSWORD`] may connect; otherwise anyone may.
    pub require_login: bool,
    /// Answer every subscription with a failure.
    pub refuse_subscriptions: bool,
}

enum Command {
    Publish(Publish),
    Drop,
}

struct Shared {
    behaviour: Behaviour,
    connects: AtomicUsize,
    client_ids: Mutex<Vec<String>>,
    subscribers: Mutex<Vec<mpsc::UnboundedSender<Command>>>,
}

/// A running fake; dropping it stops it and every connection.
pub struct FakeBroker {
    pub addr: SocketAddr,
    shared: Arc<Shared>,
    task: JoinHandle<()>,
}

impl Drop for FakeBroker {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl FakeBroker {
    pub async fn start() -> Self {
        Self::with(Behaviour::default()).await
    }

    pub async fn with(behaviour: Behaviour) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("address");
        let shared = Arc::new(Shared {
            behaviour,
            connects: AtomicUsize::new(0),
            client_ids: Mutex::new(Vec::new()),
            subscribers: Mutex::new(Vec::new()),
        });
        let accepting = Arc::clone(&shared);
        let task = tokio::spawn(async move {
            // Aborting this task drops the set, which ends every connection.
            let mut connections = JoinSet::new();
            while let Ok((stream, _)) = listener.accept().await {
                connections.spawn(serve(stream, Arc::clone(&accepting)));
            }
        });
        Self { addr, shared, task }
    }

    /// Sends `payload` on `topic` to every subscriber.
    pub fn publish(&self, topic: &str, payload: &[u8]) {
        for subscriber in self.shared.subscribers.lock().expect("lock").iter() {
            let _ = subscriber.send(Command::Publish(Publish::new(
                topic,
                QoS::AtMostOnce,
                payload,
            )));
        }
    }

    /// Closes every subscriber's connection.
    pub fn drop_connections(&self) {
        for subscriber in self.shared.subscribers.lock().expect("lock").drain(..) {
            let _ = subscriber.send(Command::Drop);
        }
    }

    /// The client id of every connection attempt, in order.
    pub fn client_ids(&self) -> Vec<String> {
        self.shared.client_ids.lock().expect("lock").clone()
    }

    /// How many connection attempts have been made, refused ones included.
    pub fn connects(&self) -> usize {
        self.shared.connects.load(Ordering::SeqCst)
    }
}

/// What a connection does after a packet.
enum Next {
    Send(Packet),
    /// Send the packet, then close.
    Refuse(Packet),
    Nothing,
    Close,
}

async fn serve(mut stream: TcpStream, shared: Arc<Shared>) {
    let (commands, mut inbox) = mpsc::unbounded_channel();
    let mut buffer = BytesMut::new();
    loop {
        tokio::select! {
            read = stream.read_buf(&mut buffer) => {
                if !matches!(read, Ok(n) if n > 0) {
                    return;
                }
                loop {
                    match Packet::read(&mut buffer, MAX) {
                        Ok(packet) => match next(&shared, &commands, packet) {
                            Next::Send(answer) => write(&mut stream, &answer).await,
                            Next::Refuse(answer) => {
                                write(&mut stream, &answer).await;
                                return;
                            }
                            Next::Nothing => {}
                            Next::Close => return,
                        },
                        Err(Error::InsufficientBytes(_)) => break,
                        Err(_) => return,
                    }
                }
            }
            Some(command) = inbox.recv() => match command {
                Command::Publish(publish) => write(&mut stream, &Packet::Publish(publish)).await,
                Command::Drop => return,
            },
        }
    }
}

fn next(shared: &Shared, commands: &mpsc::UnboundedSender<Command>, packet: Packet) -> Next {
    let behaviour = &shared.behaviour;
    match packet {
        Packet::Connect(connect) => {
            shared.connects.fetch_add(1, Ordering::SeqCst);
            shared
                .client_ids
                .lock()
                .expect("lock")
                .push(connect.client_id.clone());
            let allowed = !behaviour.require_login
                || connect
                    .login
                    .is_some_and(|login| login.username == USER && login.password == PASSWORD);
            if allowed {
                Next::Send(Packet::ConnAck(ConnAck::new(
                    ConnectReturnCode::Success,
                    false,
                )))
            } else {
                Next::Refuse(Packet::ConnAck(ConnAck::new(
                    ConnectReturnCode::BadUserNamePassword,
                    false,
                )))
            }
        }
        Packet::Subscribe(subscribe) => {
            let code = if behaviour.refuse_subscriptions {
                SubscribeReasonCode::Failure
            } else {
                shared
                    .subscribers
                    .lock()
                    .expect("lock")
                    .push(commands.clone());
                SubscribeReasonCode::Success(QoS::AtMostOnce)
            };
            Next::Send(Packet::SubAck(SubAck::new(
                subscribe.pkid,
                vec![code; subscribe.filters.len()],
            )))
        }
        Packet::PingReq => Next::Send(Packet::PingResp),
        Packet::Disconnect => Next::Close,
        _ => Next::Nothing,
    }
}

async fn write(stream: &mut TcpStream, packet: &Packet) {
    let mut out = BytesMut::new();
    if packet.write(&mut out, MAX).is_ok() {
        let _ = stream.write_all(&out).await;
    }
}
