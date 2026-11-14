//! An in-process Axis RTSP server on 127.0.0.1: Digest login, an SDP, and a synthetic H.264 stream (SPS/PPS in
//! STAP-A, IDR in FU-A, the rest single NAL units) plus the ONVIF sample document, over interleaved TCP, with Axis
//! capture times in the RTP header extension. Frames are numbered: see [`frame_index`].

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use camera::{Camera, Host, Login, Secret};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::tcp::OwnedWriteHalf;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Mutex as AsyncMutex;
use tokio::task::JoinHandle;

use super::digest::{self, Algorithm};
use super::fake_vapix::{PASSWORD, USER};
use super::rtp_packets::{rtp, video_payloads};

const REALM: &str = "AXIS_ACCC8E000001";
const NONCE: &str = "dcd98b7102dd2f0e8b11d0f600bfb0c093";
/// The camera's clock at frame 0, Unix nanoseconds (2025-03-11T08:11:54Z).
pub const FIRST_CAPTURE: i64 = 1_741_680_714_000_000_000;
/// The capture time step between frames (30 frames per second).
pub const FRAME_NANOS: i64 = 33_333_333;
/// A keyframe comes every this many frames.
pub const GOP: u32 = 10;
pub const SPS: [u8; 10] = [0x67, 0x4d, 0x00, 0x1f, 0x95, 0xa8, 0x14, 0x01, 0x6e, 0x40];
pub const PPS: [u8; 4] = [0x68, 0xee, 0x3c, 0x80];
const SDP_PARAMETER_SETS: &str = "Z00AH5WoFAFuQA==,aO48gA==";
const FIRST_RTP_TIME: u32 = 1000;

/// What goes wrong on the first connection (later ones are healthy).
#[derive(Clone, Copy)]
pub enum Fault {
    None,
    /// Close the connection after this many RTP packets.
    Drop {
        after: usize,
    },
    /// Stop sending after this many RTP packets but keep the connection open.
    Silent {
        after: usize,
    },
    /// Loses the one packet of delta frame `at` and sends no keyframe from then on, so the client never has a whole
    /// picture again though packets keep arriving.
    Damage {
        at: u32,
    },
    /// Answers DESCRIBE with this status instead of the stream.
    Describe {
        status: u16,
    },
}

#[derive(Clone)]
pub struct Behaviour {
    /// Use decodable solid-colour pictures for viewer checks.
    pub pictures: bool,
    /// Require a Digest login; otherwise every request is answered.
    pub login: bool,
    /// Offer a video stream (when the URL asks for one).
    pub video: bool,
    /// Answer every connection after the first with 401, as a camera whose login was revoked.
    pub refuse_later: bool,
    /// Offer a 2 s session and answer the keepalive that follows PLAY with 401, as a camera whose login was revoked.
    pub refuse_keepalive: bool,
    /// The session timeout offered in seconds; the client sends a keepalive every half of it (ignored with
    /// `refuse_keepalive`).
    pub session_secs: u32,
    pub first_connection: Fault,
    /// Pause between frames.
    pub interval: Duration,
    /// Frames sent back to back before the first pause.
    pub burst: u32,
    /// The `Content-Type` of the answer to DESCRIBE: none when empty, and the request's own path and query when
    /// `{target}`, as a camera that echoes what it was asked does.
    pub describe_type: &'static str,
}

impl Default for Behaviour {
    fn default() -> Self {
        Self {
            pictures: false,
            login: true,
            video: true,
            refuse_later: false,
            refuse_keepalive: false,
            session_secs: 60,
            first_connection: Fault::None,
            interval: Duration::from_millis(5),
            burst: 0,
            describe_type: "application/sdp",
        }
    }
}

/// One request the fake saw.
#[derive(Clone, Debug)]
pub struct Seen {
    pub method: String,
    pub uri: String,
    pub authorization: Option<String>,
}

struct Shared {
    behaviour: Behaviour,
    seen: Mutex<Vec<Seen>>,
    connections: AtomicUsize,
    paused: tokio::sync::watch::Sender<bool>,
    unsupported: AtomicBool,
    picture_generation: AtomicUsize,
    drop_connections: tokio::sync::watch::Sender<u64>,
}

/// A running fake; dropping it stops it.
pub struct FakeRtsp {
    pub addr: SocketAddr,
    shared: Arc<Shared>,
    task: JoinHandle<()>,
}

impl Drop for FakeRtsp {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl FakeRtsp {
    pub async fn start() -> Self {
        Self::with(Behaviour::default()).await
    }

    pub async fn with(behaviour: Behaviour) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("address");
        let shared = Arc::new(Shared {
            behaviour: behaviour.clone(),
            seen: Mutex::new(Vec::new()),
            connections: AtomicUsize::new(0),
            paused: tokio::sync::watch::channel(behaviour.pictures).0,
            unsupported: AtomicBool::new(false),
            picture_generation: AtomicUsize::new(0),
            drop_connections: tokio::sync::watch::channel(0).0,
        });
        let accepting = Arc::clone(&shared);
        let task = tokio::spawn(async move {
            // The listener owns its connections, so dropping the fixture ends held negotiations too.
            let mut connections = tokio::task::JoinSet::new();
            while let Ok((socket, _)) = listener.accept().await {
                let index = accepting.connections.fetch_add(1, Ordering::SeqCst);
                let shared = Arc::clone(&accepting);
                let mut dropped = shared.drop_connections.subscribe();
                while connections.try_join_next().is_some() {}
                connections.spawn(async move {
                    tokio::select! {
                        () = serve(socket, shared, index, addr) => {}
                        _ = dropped.changed() => {}
                    }
                });
            }
        });
        Self { addr, shared, task }
    }

    /// Hold negotiation and pictures while connecting or recovering.
    pub fn pause(&self, paused: bool) {
        self.shared.paused.send_replace(paused);
    }

    /// Advance picture identity only for an intentional recovery, never for connection attempts.
    pub fn next_picture(&self) {
        self.shared
            .picture_generation
            .fetch_add(1, Ordering::SeqCst);
    }

    /// Advertise an unsupported codec on subsequent connections.
    pub fn unsupported(&self) {
        self.shared.unsupported.store(true, Ordering::SeqCst);
    }

    /// End all current sessions, leaving the listener available for recovery.
    pub fn disconnect(&self) {
        self.shared
            .drop_connections
            .send_modify(|generation| *generation += 1);
    }

    /// A camera pointing at this fake, with the right login.
    pub fn camera(&self) -> Camera {
        let mut camera = Camera::new(Host::parse("127.0.0.1").expect("host"));
        camera.rtsp_port = self.addr.port();
        camera.timeout = Duration::from_secs(5);
        camera.login = Some(Login {
            user: USER.into(),
            password: Secret::new(PASSWORD),
        });
        camera
    }

    /// Every request seen so far.
    pub fn seen(&self) -> Vec<Seen> {
        self.shared.seen.lock().expect("lock").clone()
    }

    /// How many connections were made.
    pub fn connections(&self) -> usize {
        self.shared.connections.load(Ordering::SeqCst)
    }
}

/// The number of the frame (counted from 0) an access unit carries: its last NAL unit holds it after the header,
/// between two 0xBB bytes that keep it from looking like a start code.
pub fn frame_index(data: &[u8]) -> u32 {
    let start = data
        .windows(4)
        .rposition(|w| w == [0, 0, 0, 1])
        .expect("a start code");
    u32::from(u16::from_be_bytes([data[start + 6], data[start + 7]]))
}

/// The ONVIF document every metadata packet run carries.
pub fn sample_document() -> Vec<u8> {
    std::fs::read(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../fixtures/decoders/onvif_xml/sample_frame.xml"
    ))
    .expect("the ONVIF sample document")
}

struct Request {
    method: String,
    uri: String,
    headers: HashMap<String, String>,
}

/// Reads one request, skipping interleaved data the client sends.
async fn read_request(reader: &mut BufReader<tokio::net::tcp::OwnedReadHalf>) -> Option<Request> {
    loop {
        let first = *reader.fill_buf().await.ok()?.first()?;
        if first != b'$' {
            break;
        }
        let mut head = [0u8; 4];
        reader.read_exact(&mut head).await.ok()?;
        let mut skipped = vec![0; usize::from(u16::from_be_bytes([head[2], head[3]]))];
        reader.read_exact(&mut skipped).await.ok()?;
    }
    let mut line = String::new();
    reader.read_line(&mut line).await.ok()?;
    let mut parts = line.split_whitespace();
    let (method, uri) = (parts.next()?.to_owned(), parts.next()?.to_owned());
    let mut headers = HashMap::new();
    loop {
        let mut line = String::new();
        reader.read_line(&mut line).await.ok()?;
        let line = line.trim_end();
        if line.is_empty() {
            return Some(Request {
                method,
                uri,
                headers,
            });
        }
        let (name, value) = line.split_once(':')?;
        headers.insert(name.to_ascii_lowercase(), value.trim().to_owned());
    }
}

async fn serve(socket: TcpStream, shared: Arc<Shared>, index: usize, addr: SocketAddr) {
    let (read, write) = socket.into_split();
    let mut reader = BufReader::new(read);
    let writer = Arc::new(AsyncMutex::new(write));
    let mut channels: HashMap<&'static str, u8> = HashMap::new();
    let mut streaming = tokio::task::JoinSet::new();
    while let Some(request) = read_request(&mut reader).await {
        let authorization = request.headers.get("authorization").cloned();
        shared.seen.lock().expect("lock").push(Seen {
            method: request.method.clone(),
            uri: request.uri.clone(),
            authorization: authorization.clone(),
        });
        let cseq = request.headers.get("cseq").cloned().unwrap_or_default();
        let base = format!("rtsp://{addr}/axis-media/media.amp");
        let reply = |status: &str, headers: &str, body: &str| {
            let length = if body.is_empty() {
                String::new()
            } else {
                format!("Content-Length: {}\r\n", body.len())
            };
            format!("RTSP/1.0 {status}\r\nCSeq: {cseq}\r\n{headers}{length}\r\n{body}").into_bytes()
        };
        let authorised = !shared.behaviour.login
            || (!shared.behaviour.refuse_later || index == 0)
                && authorization.as_deref().is_some_and(|header| {
                    digest::verify(
                        header,
                        Algorithm::Md5,
                        (REALM, NONCE),
                        (USER, PASSWORD),
                        (&request.method, &request.uri),
                    )
                });
        let answer = if !authorised {
            reply(
                "401 Unauthorized",
                &format!(
                    "WWW-Authenticate: Digest realm=\"{REALM}\", qop=\"auth\", algorithm=MD5, nonce=\"{NONCE}\"\r\n"
                ),
                "",
            )
        } else {
            match request.method.as_str() {
                "DESCRIBE" => {
                    let mut paused = shared.paused.subscribe();
                    if paused.wait_for(|paused| !paused).await.is_err() {
                        return;
                    }
                    if let (0, Fault::Describe { status }) =
                        (index, shared.behaviour.first_connection)
                    {
                        writer
                            .lock()
                            .await
                            .write_all(&reply(&format!("{status} Refused"), "", ""))
                            .await
                            .ok();
                        continue;
                    }
                    let wants_video = shared.behaviour.video && !request.uri.contains("video=0");
                    let wants_metadata = request.uri.contains("analytics=polygon");
                    let mut sdp = sdp(&base, wants_video, wants_metadata);
                    if shared.unsupported.load(Ordering::SeqCst) {
                        sdp = sdp.replace("H264/90000", "VP8/90000");
                    }
                    reply(
                        "200 OK",
                        &format!(
                            "Content-Base: {base}/\r\n{}",
                            match shared.behaviour.describe_type {
                                "" => String::new(),
                                "{target}" => format!(
                                    "Content-Type: {}\r\n",
                                    request.uri.split_once("://").map_or("", |(_, rest)| {
                                        rest.find('/').map_or("", |at| &rest[at..])
                                    })
                                ),
                                other => format!("Content-Type: {other}\r\n"),
                            }
                        ),
                        &sdp,
                    )
                }
                "SETUP" => {
                    let track = if request.uri.ends_with("trackID=1") {
                        "video"
                    } else {
                        "metadata"
                    };
                    let transport = request
                        .headers
                        .get("transport")
                        .cloned()
                        .unwrap_or_default();
                    let interleaved = transport.split("interleaved=").nth(1).unwrap_or("0-1");
                    let first: u8 = interleaved
                        .split('-')
                        .next()
                        .and_then(|n| n.parse().ok())
                        .unwrap_or(0);
                    channels.insert(track, first);
                    let timeout = if shared.behaviour.refuse_keepalive {
                        2
                    } else {
                        shared.behaviour.session_secs
                    };
                    reply(
                        "200 OK",
                        &format!(
                            "Transport: RTP/AVP/TCP;unicast;interleaved={first}-{};ssrc=0000000A\r\nSession: 12345678;timeout={timeout}\r\n",
                            first + 1
                        ),
                        "",
                    )
                }
                "PLAY" => {
                    let info = format!(
                        "RTP-Info: url={base}/trackID=1;seq=0;rtptime={FIRST_RTP_TIME},url={base}/trackID=2;seq=0;rtptime={FIRST_RTP_TIME}\r\n"
                    );
                    let answer = reply("200 OK", &format!("Session: 12345678\r\n{info}"), "");
                    writer.lock().await.write_all(&answer).await.ok();
                    let fault = if index == 0 {
                        shared.behaviour.first_connection
                    } else {
                        Fault::None
                    };
                    streaming.spawn(stream(
                        Arc::clone(&writer),
                        channels.clone(),
                        shared.behaviour.clone(),
                        fault,
                        Arc::clone(&shared),
                        shared.picture_generation.load(Ordering::SeqCst),
                    ));
                    continue;
                }
                _ if !streaming.is_empty() && shared.behaviour.refuse_keepalive => {
                    reply("401 Unauthorized", "", "")
                }
                _ => reply("200 OK", "Session: 12345678\r\n", ""),
            }
        };
        if writer.lock().await.write_all(&answer).await.is_err() {
            break;
        }
    }
}

fn sdp(base: &str, video: bool, metadata: bool) -> String {
    let mut sdp =
        format!("v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=Fake\r\nt=0 0\r\na=control:{base}\r\n");
    if video {
        sdp += &format!(
            "m=video 0 RTP/AVP 96\r\na=control:{base}/trackID=1\r\na=rtpmap:96 H264/90000\r\n\
             a=fmtp:96 packetization-mode=1;profile-level-id=4D001F;sprop-parameter-sets={SDP_PARAMETER_SETS}\r\n"
        );
    }
    if metadata {
        sdp += &format!(
            "m=application 0 RTP/AVP 107\r\na=control:{base}/trackID=2\r\na=rtpmap:107 vnd.onvif.metadata/90000\r\n"
        );
    }
    sdp
}

/// Sends frames until the client goes away; the fault, if any, strikes after its number of RTP packets.
async fn stream(
    writer: Arc<AsyncMutex<OwnedWriteHalf>>,
    channels: HashMap<&'static str, u8>,
    behaviour: Behaviour,
    fault: Fault,
    shared: Arc<Shared>,
    generation: usize,
) {
    let mut sent = 0usize;
    let mut seq = [0u16; 2];
    let document = sample_document();
    for frame in 0u32.. {
        if frame >= behaviour.burst {
            tokio::time::sleep(behaviour.interval).await;
        }
        if *shared.paused.borrow() {
            continue;
        }
        let capture = FIRST_CAPTURE + i64::from(frame) * FRAME_NANOS;
        let rtp_time = FIRST_RTP_TIME + frame * 3000;
        let mut packets: Vec<(u8, usize, bool, Vec<u8>)> = Vec::new();
        if let Some(&channel) = channels.get("video") {
            let keyframes = !matches!(fault, Fault::Damage { at } if frame >= at);
            let payloads = if behaviour.pictures {
                super::pictures::payloads(generation)
            } else {
                video_payloads(frame, keyframes)
            };
            for (payload, marker) in payloads {
                packets.push((channel, 0, marker, payload));
            }
        }
        if let Some(&channel) = channels.get("metadata") {
            let chunks: Vec<&[u8]> = document.chunks(1000).collect();
            for (n, chunk) in chunks.iter().enumerate() {
                packets.push((channel, 1, n + 1 == chunks.len(), chunk.to_vec()));
            }
        }
        for (channel, lane, marker, payload) in packets {
            match fault {
                Fault::Drop { after } if sent >= after => {
                    writer.lock().await.shutdown().await.ok();
                    return;
                }
                Fault::Silent { after } if sent >= after => {
                    std::future::pending::<()>().await;
                }
                _ => {}
            }
            if lane == 0 && matches!(fault, Fault::Damage { at } if frame == at) {
                seq[lane] = seq[lane].wrapping_add(1);
                continue;
            }
            let packet = rtp(
                if lane == 0 { 96 } else { 107 },
                seq[lane],
                rtp_time,
                marker,
                capture,
                &payload,
            );
            seq[lane] = seq[lane].wrapping_add(1);
            let mut framed = vec![b'$', channel];
            framed.extend(u16::try_from(packet.len()).expect("small").to_be_bytes());
            framed.extend(packet);
            if writer.lock().await.write_all(&framed).await.is_err() {
                return;
            }
            sent += 1;
        }
    }
}
