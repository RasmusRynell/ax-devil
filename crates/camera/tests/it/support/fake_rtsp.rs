//! An in-process Axis RTSP server on 127.0.0.1: Digest login, an SDP, and a synthetic H.264 stream (SPS/PPS in
//! STAP-A, IDR in FU-A, the rest single NAL units) plus the ONVIF sample document, over interleaved TCP, with Axis
//! capture times in the RTP header extension. Frames are numbered: see [`frame_index`].

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
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
}

#[derive(Clone)]
pub struct Behaviour {
    /// Require a Digest login; otherwise every request is answered.
    pub login: bool,
    /// Offer a video stream (when the URL asks for one).
    pub video: bool,
    pub first_connection: Fault,
    /// Pause between frames.
    pub interval: Duration,
    /// Frames sent back to back before the first pause.
    pub burst: u32,
}

impl Default for Behaviour {
    fn default() -> Self {
        Self {
            login: true,
            video: true,
            first_connection: Fault::None,
            interval: Duration::from_millis(5),
            burst: 0,
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
            behaviour,
            seen: Mutex::new(Vec::new()),
            connections: AtomicUsize::new(0),
        });
        let accepting = Arc::clone(&shared);
        let task = tokio::spawn(async move {
            while let Ok((socket, _)) = listener.accept().await {
                let index = accepting.connections.fetch_add(1, Ordering::SeqCst);
                tokio::spawn(serve(socket, Arc::clone(&accepting), index, addr));
            }
        });
        Self { addr, shared, task }
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
    let mut streaming: Option<JoinHandle<()>> = None;
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
            || authorization.as_deref().is_some_and(|header| {
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
                    let wants_video = shared.behaviour.video && !request.uri.contains("video=0");
                    let wants_metadata = request.uri.contains("analytics=polygon");
                    let sdp = sdp(&base, wants_video, wants_metadata);
                    reply(
                        "200 OK",
                        &format!("Content-Base: {base}/\r\nContent-Type: application/sdp\r\n"),
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
                    reply(
                        "200 OK",
                        &format!(
                            "Transport: RTP/AVP/TCP;unicast;interleaved={first}-{};ssrc=0000000A\r\nSession: 12345678;timeout=60\r\n",
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
                    streaming = Some(tokio::spawn(stream(
                        Arc::clone(&writer),
                        channels.clone(),
                        shared.behaviour.clone(),
                        fault,
                    )));
                    continue;
                }
                _ => reply("200 OK", "Session: 12345678\r\n", ""),
            }
        };
        if writer.lock().await.write_all(&answer).await.is_err() {
            break;
        }
    }
    if let Some(task) = streaming {
        task.abort();
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
) {
    let mut sent = 0usize;
    let mut seq = [0u16; 2];
    let document = sample_document();
    for frame in 0u32.. {
        if frame >= behaviour.burst {
            tokio::time::sleep(behaviour.interval).await;
        }
        let capture = FIRST_CAPTURE + i64::from(frame) * FRAME_NANOS;
        let rtp_time = FIRST_RTP_TIME + frame * 3000;
        let mut packets: Vec<(u8, usize, bool, Vec<u8>)> = Vec::new();
        if let Some(&channel) = channels.get("video") {
            let keyframes = !matches!(fault, Fault::Damage { at } if frame >= at);
            for (payload, marker) in video_payloads(frame, keyframes) {
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

/// The RTP payloads of frame `frame`, each with whether it ends the frame.
fn video_payloads(frame: u32, keyframes: bool) -> Vec<(Vec<u8>, bool)> {
    let index = u16::try_from(frame)
        .expect("fewer than 65536 frames")
        .to_be_bytes();
    if !keyframes || !frame.is_multiple_of(GOP) {
        let mut nal = vec![0x41, 0xBB, index[0], index[1], 0xBB];
        nal.extend([0xAA; 200]);
        return vec![(nal, true)];
    }
    let mut stap = vec![24, 0, SPS.len() as u8];
    stap.extend(SPS);
    stap.extend([0, PPS.len() as u8]);
    stap.extend(PPS);
    let mut idr = vec![0x65, 0xBB, index[0], index[1], 0xBB];
    idr.extend([0xAA; 2500]);
    // FU-A: the NAL header's type and importance go to the fragments' headers; its body is cut into pieces.
    let body = &idr[1..];
    let pieces: Vec<&[u8]> = body.chunks(1000).collect();
    let mut payloads = vec![(stap, false)];
    for (n, piece) in pieces.iter().enumerate() {
        let (first, last) = (n == 0, n + 1 == pieces.len());
        let header = (u8::from(first) << 7) | (u8::from(last) << 6) | 5;
        let mut payload = vec![0x60 | 28, header];
        payload.extend(*piece);
        payloads.push((payload, last));
    }
    payloads
}

/// One RTP packet with the Axis capture time in its header extension.
fn rtp(
    payload_type: u8,
    seq: u16,
    timestamp: u32,
    marker: bool,
    capture: i64,
    payload: &[u8],
) -> Vec<u8> {
    let seconds = u32::try_from(capture / 1_000_000_000 + 2_208_988_800).expect("NTP seconds");
    let fraction =
        u32::try_from((u128::from(capture.unsigned_abs() % 1_000_000_000) << 32) / 1_000_000_000)
            .expect("fraction");
    let mut packet = vec![0x90, (u8::from(marker) << 7) | payload_type];
    packet.extend(seq.to_be_bytes());
    packet.extend(timestamp.to_be_bytes());
    packet.extend(0xAu32.to_be_bytes());
    packet.extend([0xAB, 0xAC, 0, 3]);
    packet.extend(seconds.to_be_bytes());
    packet.extend(fraction.to_be_bytes());
    packet.extend([0; 4]);
    packet.extend(payload);
    packet
}
