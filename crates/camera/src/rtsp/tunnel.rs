//! The camera's RTSP-over-HTTP tunnel: a GET connection whose body is the RTSP stream from the camera, and a POST
//! connection whose body is the RTSP stream to it, in base64. Both carry one session cookie. Chunked responses and
//! redirects are not supported, and the POST announces a fixed length (32767 bytes) that it never renews.

use std::sync::Arc;
use std::time::Duration;

use bytes::{Bytes, BytesMut};
use rustls::pki_types::ServerName;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, ReadHalf, WriteHalf, split};
use tokio::net::TcpStream;
use tokio::time::timeout;
use tokio_rustls::TlsConnector;
use url::Host;

use super::endpoint::{Target, Transport};
use crate::auth::{Responder, Signed};
use crate::error::{CameraError, ErrorKind};
use crate::tls;

const USER_AGENT: &str = "ax-devil";
/// The longest response head read; a camera's is a few hundred bytes.
const MAX_HEAD: usize = 16 * 1024;
/// The length the POST announces, as `ffmpeg -rtsp_transport http` and the camera's own tools do.
const POST_LENGTH: u32 = 32767;

/// A connection, plain or TLS.
pub(super) trait Io: AsyncRead + AsyncWrite + Send + Unpin {}
impl<T: AsyncRead + AsyncWrite + Send + Unpin> Io for T {}
type Wire = Box<dyn Io>;

/// An open tunnel.
pub(super) struct Tunnel {
    /// What the camera sends, after the `leftover` bytes that came with the response head.
    pub(super) get: ReadHalf<Wire>,
    pub(super) leftover: Bytes,
    /// Where the base64 of what is sent to the camera goes.
    pub(super) post: WriteHalf<Wire>,
}

/// What the response to the GET says.
struct Head {
    status: u16,
    /// The `WWW-Authenticate` values.
    challenges: Vec<String>,
    chunked: bool,
}

/// Opens both connections of the tunnel to `target` within `limit`.
pub(super) async fn connect(target: &Target, limit: Duration) -> Result<Tunnel, CameraError> {
    match timeout(limit, establish(target)).await {
        Ok(tunnel) => tunnel,
        Err(_) => Err(CameraError::new(
            ErrorKind::TimedOut,
            format!("no answer within {} s", limit.as_secs_f32()),
        )),
    }
}

async fn establish(target: &Target) -> Result<Tunnel, CameraError> {
    let mut cookie = [0u8; 8];
    getrandom::fill(&mut cookie).map_err(|_| {
        CameraError::new(ErrorKind::Unsupported, "no random numbers on this machine")
    })?;
    let cookie: String = cookie.iter().map(|byte| format!("{byte:02x}")).collect();
    let uri = target.request_target();
    let mut responder: Option<Responder<'_>> = None;
    let mut authorization = None;
    // At most two tries: the second answers the camera's challenge.
    let (get, leftover) = loop {
        let mut wire = open(target).await?;
        let headers = "Accept: application/x-rtsp-tunnelled\r\n";
        send(
            &mut wire,
            &request("GET", target, &cookie, headers, authorization.as_deref()),
        )
        .await?;
        let (head, leftover) = read_head(&mut wire).await?;
        match (head.status, &target.login) {
            (200, _) if head.chunked => {
                return Err(CameraError::new(
                    ErrorKind::Unsupported,
                    "the tunnel is chunked, which is not supported",
                ));
            }
            (200, _) => break (split(wire).0, leftover),
            (401, Some(login)) if responder.is_none() => {
                let challenges: Vec<&str> = head.challenges.iter().map(String::as_str).collect();
                let mut answering = Responder::new(&challenges, login, true)?;
                authorization = Some(answering.answer(&signed("GET", &uri))?);
                responder = Some(answering);
            }
            (401, Some(_)) => {
                return Err(CameraError::new(ErrorKind::LoginRejected, "HTTP 401"));
            }
            (401, None) => {
                return Err(CameraError::new(ErrorKind::LoginRequired, "HTTP 401"));
            }
            (status, _) => {
                return Err(CameraError::new(
                    ErrorKind::of_status(status),
                    format!("HTTP {status}"),
                ));
            }
        }
    };
    // The POST reuses the GET's challenge with the next nonce count.
    let authorization = responder
        .as_mut()
        .map(|responder| responder.answer(&signed("POST", &uri)))
        .transpose()?;
    let headers = format!(
        "Content-Type: application/x-rtsp-tunnelled\r\nContent-Length: {POST_LENGTH}\r\nExpires: Sun, 9 Jan 1972 00:00:00 GMT\r\n"
    );
    let mut wire = open(target).await?;
    send(
        &mut wire,
        &request("POST", target, &cookie, &headers, authorization.as_deref()),
    )
    .await?;
    Ok(Tunnel {
        get,
        leftover,
        post: split(wire).1,
    })
}

fn signed<'a>(method: &'a str, uri: &'a str) -> Signed<'a> {
    Signed {
        method,
        uri,
        // The POST streams: its body cannot be signed, so only `auth` answers a challenge.
        body: None,
    }
}

/// A request head: the lines every tunnel request has, then `headers`, then the login if there is one.
fn request(
    method: &str,
    target: &Target,
    cookie: &str,
    headers: &str,
    authorization: Option<&str>,
) -> String {
    let host = target.url.host_str().unwrap_or_default();
    let authority = match target.url.port() {
        Some(port) => format!("{host}:{port}"),
        None => host.to_owned(),
    };
    let mut text = format!(
        "{method} {} HTTP/1.0\r\nHost: {authority}\r\nUser-Agent: {USER_AGENT}\r\nx-sessioncookie: {cookie}\r\nPragma: no-cache\r\nCache-Control: no-cache\r\n{headers}",
        target.request_target()
    );
    if let Some(authorization) = authorization {
        text.push_str(&format!("Authorization: {authorization}\r\n"));
    }
    text.push_str("\r\n");
    text
}

async fn open(target: &Target) -> Result<Wire, CameraError> {
    let host = match target.url.host() {
        Some(Host::Domain(name)) => name.to_owned(),
        Some(Host::Ipv4(address)) => address.to_string(),
        Some(Host::Ipv6(address)) => address.to_string(),
        None => {
            return Err(CameraError::new(
                ErrorKind::InvalidTarget,
                "the stream URL has no host",
            ));
        }
    };
    let port = target
        .url
        .port_or_known_default()
        .ok_or_else(|| CameraError::new(ErrorKind::InvalidTarget, "the stream URL has no port"))?;
    let tcp = TcpStream::connect((host.as_str(), port))
        .await
        .map_err(|error| CameraError::io(ErrorKind::Unreachable, &error))?;
    // Small writes (one RTSP request each) must not wait for more.
    tcp.set_nodelay(true).ok();
    if target.transport != Transport::Https {
        return Ok(Box::new(tcp));
    }
    let name = ServerName::try_from(host).map_err(|_| {
        CameraError::new(ErrorKind::InvalidTarget, "the host is not a valid TLS name")
    })?;
    let tls = TlsConnector::from(Arc::new(tls::client_config()?))
        .connect(name, tcp)
        .await
        .map_err(|_| CameraError::new(ErrorKind::Unreachable, "the TLS handshake failed"))?;
    Ok(Box::new(tls))
}

async fn send(wire: &mut Wire, text: &str) -> Result<(), CameraError> {
    let lost = |error: std::io::Error| CameraError::io(ErrorKind::Lost, &error);
    wire.write_all(text.as_bytes()).await.map_err(lost)?;
    wire.flush().await.map_err(lost)
}

/// Reads the response head; the bytes that arrived after it are the start of the body.
async fn read_head(wire: &mut Wire) -> Result<(Head, Bytes), CameraError> {
    let mut buffer = BytesMut::new();
    loop {
        if let Some(end) = buffer.windows(4).position(|window| window == b"\r\n\r\n") {
            let head = parse_head(&buffer[..end])?;
            return Ok((head, Bytes::copy_from_slice(&buffer[end + 4..])));
        }
        if buffer.len() > MAX_HEAD {
            return Err(malformed());
        }
        match wire.read_buf(&mut buffer).await {
            Ok(0) => {
                return Err(CameraError::new(
                    ErrorKind::Lost,
                    "the camera closed the connection",
                ));
            }
            Ok(_) => {}
            Err(error) => return Err(CameraError::io(ErrorKind::Lost, &error)),
        }
    }
}

fn malformed() -> CameraError {
    CameraError::new(ErrorKind::Malformed, "the answer is not HTTP")
}

fn parse_head(bytes: &[u8]) -> Result<Head, CameraError> {
    let text = String::from_utf8_lossy(bytes);
    let mut lines = text.split("\r\n");
    let mut status_line = lines.next().unwrap_or_default().split_whitespace();
    let status = match (status_line.next(), status_line.next()) {
        (Some(version), Some(code)) if version.starts_with("HTTP/") => {
            code.parse().map_err(|_| malformed())?
        }
        _ => return Err(malformed()),
    };
    let mut head = Head {
        status,
        challenges: Vec::new(),
        chunked: false,
    };
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        match name.trim().to_ascii_lowercase().as_str() {
            "www-authenticate" => head.challenges.push(value.trim().to_owned()),
            "transfer-encoding" => {
                head.chunked |= value.to_ascii_lowercase().contains("chunked");
            }
            _ => {}
        }
    }
    Ok(head)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_response_head_gives_status_challenges_and_chunking() {
        let head = parse_head(
            b"HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Digest realm=\"a\", nonce=\"n\"\r\nwww-authenticate: Basic realm=\"a\"\r\nTransfer-Encoding: Chunked",
        )
        .unwrap();
        assert_eq!(head.status, 401);
        assert_eq!(head.challenges.len(), 2);
        assert!(head.chunked);
        for bad in [&b"RTSP/1.0 200 OK"[..], b"HTTP/1.0", b"HTTP/1.0 ok", b""] {
            assert_eq!(
                parse_head(bad).err().map(|e| e.kind),
                Some(ErrorKind::Malformed)
            );
        }
    }
}
