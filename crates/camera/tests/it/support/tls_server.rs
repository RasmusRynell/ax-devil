//! A camera on HTTPS with a self-signed certificate that offers only Basic authentication (and answers "who are you"
//! without a login), to check what the client accepts and sends over TLS.

use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

use rustls::ServerConfig;
use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::task::JoinHandle;
use tokio_rustls::TlsAcceptor;

const INFO: &str = r#"{"data":{"propertyList":{"Brand":"AXIS","ProdShortName":"AXIS M3086","SerialNumber":"ACCC8E000002"}}}"#;

/// A running fake; dropping it stops it.
pub struct BasicOverTls {
    pub addr: SocketAddr,
    /// The `Authorization` header of every request that had one.
    pub authorizations: Arc<Mutex<Vec<String>>>,
    task: JoinHandle<()>,
}

impl Drop for BasicOverTls {
    fn drop(&mut self) {
        self.task.abort();
    }
}

/// A TLS acceptor with a fresh self-signed certificate, which no client could verify.
pub fn acceptor() -> TlsAcceptor {
    let certified =
        rcgen::generate_simple_self_signed(vec!["localhost".to_owned()]).expect("certificate");
    let key = PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(
        certified.signing_key.serialize_der(),
    ));
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .expect("versions")
        .with_no_client_auth()
        .with_single_cert(vec![certified.cert.der().clone()], key)
        .expect("server config");
    TlsAcceptor::from(Arc::new(config))
}

/// Lets `axum::serve` answer over TLS; a connection whose handshake fails is dropped.
pub struct TlsListener {
    pub tcp: TcpListener,
    pub acceptor: TlsAcceptor,
}

impl axum::serve::Listener for TlsListener {
    type Io = tokio_rustls::server::TlsStream<tokio::net::TcpStream>;
    type Addr = SocketAddr;

    async fn accept(&mut self) -> (Self::Io, Self::Addr) {
        loop {
            let Ok((stream, addr)) = self.tcp.accept().await else {
                continue;
            };
            if let Ok(tls) = self.acceptor.accept(stream).await {
                return (tls, addr);
            }
        }
    }

    fn local_addr(&self) -> std::io::Result<Self::Addr> {
        self.tcp.local_addr()
    }
}

impl BasicOverTls {
    pub async fn start() -> Self {
        let acceptor = acceptor();
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("address");
        let authorizations = Arc::new(Mutex::new(Vec::new()));
        let log = authorizations.clone();
        let task = tokio::spawn(async move {
            loop {
                let (stream, _) = listener.accept().await.expect("accept");
                let (acceptor, log) = (acceptor.clone(), log.clone());
                tokio::spawn(async move {
                    if let Ok(tls) = acceptor.accept(stream).await {
                        serve(tls, &log).await;
                    }
                });
            }
        });
        Self {
            addr,
            authorizations,
            task,
        }
    }
}

/// Answers requests on one connection: 401 until the request carries an `Authorization` header, then the info.
async fn serve<S: AsyncReadExt + AsyncWriteExt + Unpin>(mut stream: S, log: &Mutex<Vec<String>>) {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 2048];
    loop {
        let Ok(read) = stream.read(&mut chunk).await else {
            return;
        };
        if read == 0 {
            return;
        }
        buffer.extend_from_slice(&chunk[..read]);
        let text = String::from_utf8_lossy(&buffer).into_owned();
        let Some((head, body)) = text.split_once("\r\n\r\n") else {
            continue;
        };
        let length = head
            .lines()
            .find_map(|line| {
                line.to_ascii_lowercase()
                    .strip_prefix("content-length:")
                    .and_then(|v| v.trim().parse().ok())
            })
            .unwrap_or(0usize);
        if body.len() < length {
            continue;
        }
        let authorization = head.lines().find_map(|line| {
            line.to_ascii_lowercase()
                .starts_with("authorization:")
                .then(|| line[14..].trim().to_owned())
        });
        let reply = match authorization {
            Some(value) => {
                log.lock().expect("lock").push(value);
                format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{INFO}", INFO.len())
            }
            // As a real camera does: "who are you" needs no login.
            None if body.contains("getAllUnrestrictedProperties") => {
                format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{INFO}", INFO.len())
            }
            None => "HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm=\"cam\"\r\nContent-Length: 0\r\n\r\n".to_owned(),
        };
        if stream.write_all(reply.as_bytes()).await.is_err() {
            return;
        }
        buffer.clear();
    }
}
