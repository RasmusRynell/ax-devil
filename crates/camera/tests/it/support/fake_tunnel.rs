//! An in-process Axis web server on 127.0.0.1 that carries RTSP over HTTP or HTTPS (`x-rtsp-tunnelled`) into a
//! [`FakeRtsp`]. Both the GET and the POST need a Digest login (the GET's nonce count 1, the POST's 2), the two are
//! paired by their session cookie, and every POST message is decoded as strict base64 before it reaches the RTSP fake.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use camera::{Camera, Scheme, Web};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, ReadHalf, split};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::oneshot;
use tokio::task::JoinHandle;

use super::digest::{self, Algorithm};
use super::fake_rtsp::{Behaviour, FakeRtsp};
use super::fake_vapix::{PASSWORD, USER};
use super::tls_server;

const REALM: &str = "AXIS_TUNNEL";
const NONCE: &str = "6f1d0b7a4c2e9d83b5a07c1e2f4d6a8b";

trait Io: AsyncRead + AsyncWrite + Send + Unpin + 'static {}
impl<T: AsyncRead + AsyncWrite + Send + Unpin + 'static> Io for T {}
type Conn = Box<dyn Io>;
/// A POST connection once it is paired: what is left to read of it, and the body bytes that came with its head.
type Post = (ReadHalf<Conn>, Vec<u8>);

#[derive(Default)]
pub struct Options {
    /// Serve HTTPS with a self-signed certificate.
    pub tls: bool,
    /// Close the first tunnel when its POST has carried this many bytes: the camera's 32767 budget, made small.
    pub post_budget: Option<usize>,
    /// The `qop` the Digest challenge offers; `auth` when `None`.
    pub qop: Option<&'static str>,
    /// The RTSP server behind the tunnel.
    pub rtsp: Behaviour,
}

/// One HTTP request the fake saw.
#[derive(Clone, Debug)]
pub struct Seen {
    pub method: String,
    pub uri: String,
    pub cookie: String,
    pub authorization: Option<String>,
}

struct Request {
    method: String,
    uri: String,
    headers: HashMap<String, String>,
    /// Bytes that came after the head.
    rest: Vec<u8>,
}

struct Shared {
    qop: &'static str,
    budget: Option<usize>,
    rtsp: SocketAddr,
    seen: Mutex<Vec<Seen>>,
    /// GET connections waiting for their POST, by cookie.
    waiting: Mutex<HashMap<String, oneshot::Sender<Post>>>,
    tunnels: AtomicUsize,
    closed: AtomicUsize,
    bad_base64: AtomicUsize,
}

/// A running fake; dropping it stops it.
pub struct FakeTunnel {
    pub addr: SocketAddr,
    pub rtsp: FakeRtsp,
    scheme: Scheme,
    shared: Arc<Shared>,
    task: JoinHandle<()>,
}

impl Drop for FakeTunnel {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl FakeTunnel {
    pub async fn start(options: Options) -> Self {
        let rtsp = FakeRtsp::with(options.rtsp).await;
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("address");
        let shared = Arc::new(Shared {
            qop: options.qop.unwrap_or("auth"),
            budget: options.post_budget,
            rtsp: rtsp.addr,
            seen: Mutex::new(Vec::new()),
            waiting: Mutex::new(HashMap::new()),
            tunnels: AtomicUsize::new(0),
            closed: AtomicUsize::new(0),
            bad_base64: AtomicUsize::new(0),
        });
        let acceptor = options.tls.then(tls_server::acceptor);
        let accepting = Arc::clone(&shared);
        let task = tokio::spawn(async move {
            while let Ok((tcp, _)) = listener.accept().await {
                let (shared, acceptor) = (Arc::clone(&accepting), acceptor.clone());
                tokio::spawn(async move {
                    let conn: Conn = match acceptor {
                        Some(acceptor) => match acceptor.accept(tcp).await {
                            Ok(tls) => Box::new(tls),
                            Err(_) => return,
                        },
                        None => Box::new(tcp),
                    };
                    handle(conn, shared).await;
                });
            }
        });
        let scheme = if options.tls {
            Scheme::Https
        } else {
            Scheme::Http
        };
        Self {
            addr,
            rtsp,
            scheme,
            shared,
            task,
        }
    }

    /// A camera whose web interface is this fake (its RTSP port is closed), with the right login.
    pub fn camera(&self) -> Camera {
        let mut camera = self.rtsp.camera();
        camera.rtsp_port = 1;
        camera.web = Web {
            scheme: self.scheme,
            port: self.addr.port(),
        };
        camera
    }

    /// Every HTTP request seen so far, in order.
    pub fn seen(&self) -> Vec<Seen> {
        self.shared.seen.lock().expect("lock").clone()
    }

    /// Tunnels whose client closed both the GET and the POST.
    pub fn closed(&self) -> usize {
        self.shared.closed.load(Ordering::SeqCst)
    }

    /// POST messages that were not strict base64.
    pub fn bad_base64(&self) -> usize {
        self.shared.bad_base64.load(Ordering::SeqCst)
    }
}

async fn read_request(conn: &mut Conn) -> Option<Request> {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 2048];
    let end = loop {
        if let Some(end) = buffer.windows(4).position(|w| w == b"\r\n\r\n") {
            break end;
        }
        let read = conn.read(&mut chunk).await.ok().filter(|&n| n > 0)?;
        buffer.extend_from_slice(&chunk[..read]);
    };
    let head = String::from_utf8_lossy(&buffer[..end]).into_owned();
    let mut lines = head.split("\r\n");
    let mut line = lines.next()?.split_whitespace();
    let (method, uri) = (line.next()?.to_owned(), line.next()?.to_owned());
    let headers = lines
        .filter_map(|line| line.split_once(':'))
        .map(|(name, value)| (name.to_ascii_lowercase(), value.trim().to_owned()))
        .collect();
    Some(Request {
        method,
        uri,
        headers,
        rest: buffer[end + 4..].to_vec(),
    })
}

async fn handle(mut conn: Conn, shared: Arc<Shared>) {
    let Some(request) = read_request(&mut conn).await else {
        return;
    };
    let cookie = request
        .headers
        .get("x-sessioncookie")
        .cloned()
        .unwrap_or_default();
    shared.seen.lock().expect("lock").push(Seen {
        method: request.method.clone(),
        uri: request.uri.clone(),
        cookie: cookie.clone(),
        authorization: request.headers.get("authorization").cloned(),
    });
    let nc = if request.method == "GET" {
        "00000001"
    } else {
        "00000002"
    };
    if !authorised(&request, nc) {
        let challenge = format!(
            "HTTP/1.0 401 Unauthorized\r\nWWW-Authenticate: Digest realm=\"{REALM}\", qop=\"{}\", algorithm=MD5, nonce=\"{NONCE}\"\r\nContent-Length: 0\r\n\r\n",
            shared.qop
        );
        conn.write_all(challenge.as_bytes()).await.ok();
        return;
    }
    match request.method.as_str() {
        "GET" => {
            let (post_tx, post_rx) = oneshot::channel();
            shared.waiting.lock().expect("lock").insert(cookie, post_tx);
            let ok = "HTTP/1.0 200 OK\r\nContent-Type: application/x-rtsp-tunnelled\r\n\r\n";
            if conn.write_all(ok.as_bytes()).await.is_err() {
                return;
            }
            if let Ok(post) = post_rx.await {
                pump(conn, post, &shared).await;
            }
        }
        "POST" => {
            let waiting = shared.waiting.lock().expect("lock").remove(&cookie);
            match waiting {
                Some(get) => {
                    get.send((split(conn).0, request.rest)).ok();
                }
                None => {
                    conn.write_all(b"HTTP/1.0 400 Bad Request\r\n\r\n")
                        .await
                        .ok();
                }
            }
        }
        _ => {}
    }
}

fn authorised(request: &Request, nc: &str) -> bool {
    let Some(header) = request.headers.get("authorization") else {
        return false;
    };
    digest::verify(
        header,
        Algorithm::Md5,
        (REALM, NONCE),
        (USER, PASSWORD),
        (&request.method, &request.uri),
    ) && digest::fields(header).is_some_and(|fields| fields.contains(&("nc".into(), nc.into())))
}

/// What ended the POST side.
enum End {
    /// The client closed the POST.
    Closed,
    /// The camera ends the tunnel itself: its POST budget is spent, or the body is not base64.
    Camera,
}

/// Joins a paired GET and POST to the RTSP server until one side ends.
async fn pump(get: Conn, (mut post, leftover): Post, shared: &Shared) {
    let index = shared.tunnels.fetch_add(1, Ordering::SeqCst);
    let budget = shared.budget.filter(|_| index == 0);
    let Ok(rtsp) = TcpStream::connect(shared.rtsp).await else {
        return;
    };
    let (mut rtsp_read, mut rtsp_write) = rtsp.into_split();
    let (mut get_read, mut get_write) = split(get);
    // Nothing is read from the GET but its end, which is the client closing it.
    let get_closed = async move {
        let mut sink = [0u8; 64];
        while matches!(get_read.read(&mut sink).await, Ok(n) if n > 0) {}
    };
    let upstream = async {
        let mut pending = leftover;
        let mut total = pending.len();
        let mut chunk = [0u8; 1024];
        loop {
            // Each message was encoded on its own, so every group of four characters decodes on its own.
            while pending.len() >= 4 {
                let group: Vec<u8> = pending.drain(..4).collect();
                let Ok(bytes) = STANDARD.decode(&group) else {
                    shared.bad_base64.fetch_add(1, Ordering::SeqCst);
                    return End::Camera;
                };
                if rtsp_write.write_all(&bytes).await.is_err() {
                    return End::Camera;
                }
            }
            if budget.is_some_and(|budget| total >= budget) {
                return End::Camera;
            }
            match post.read(&mut chunk).await {
                Ok(n) if n > 0 => {
                    total += n;
                    pending.extend_from_slice(&chunk[..n]);
                }
                _ => return End::Closed,
            }
        }
    };
    let downstream = tokio::io::copy(&mut rtsp_read, &mut get_write);
    tokio::select! {
        _ = downstream => {}
        end = upstream => {
            if matches!(end, End::Closed) {
                get_closed.await;
                shared.closed.fetch_add(1, Ordering::SeqCst);
            }
        }
    }
}
