//! One DataHub WebSocket: a session token from the device API, the upgrade, JSON-RPC calls and a heartbeat.

use std::collections::VecDeque;
use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use reqwest::Method;
use tokio::net::TcpStream;
use tokio::time::{Instant, sleep_until, timeout};
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
use tokio_tungstenite::tungstenite::{Error, Message};
use tokio_tungstenite::{
    Connector, MaybeTlsStream, WebSocketStream, connect_async_tls_with_config,
};
use tracing::warn;
use url::Url;

use super::protocol::{self, Incoming, Sample, protocol};
use crate::device::{Call, Device};
use crate::error::{CameraError, ErrorKind};
use crate::target::Scheme;
use crate::tls;

/// How often an idle connection is pinged. Anything received counts as a sign of life; a ping unanswered for half
/// this long ends the connection. A quiet topic is not an error, a dead connection is.
const HEARTBEAT: Duration = Duration::from_secs(30);
/// How long a polite close may take.
const CLOSE: Duration = Duration::from_secs(1);
/// The largest frame and the largest assembled message accepted. A sample is a scene frame of a few kilobytes, so this
/// is generous; it bounds what a connected endpoint can make this process hold.
const LARGEST_MESSAGE: usize = 4 << 20;
/// How many samples that arrive before the subscription is confirmed are kept; the oldest are dropped first.
const EARLY: usize = 64;

/// Samples that arrived before a request was answered: at most [`EARLY`], and how many older ones were dropped.
#[derive(Default)]
pub(super) struct Early {
    pub(super) samples: VecDeque<Sample>,
    pub(super) dropped: u64,
}

/// An open DataHub connection.
pub(super) struct Socket {
    stream: WebSocketStream<MaybeTlsStream<TcpStream>>,
    timeout: Duration,
    requests: u32,
    next_ping: Instant,
    pong_due: Option<Instant>,
}

impl Socket {
    /// Asks the device for a session token (Digest on the 401, like every device call), then upgrades to the data-hub
    /// API on the same host and port. Neither step follows a redirect.
    pub(super) async fn open(device: &Device) -> Result<Self, CameraError> {
        let camera = &device.camera;
        let token = device
            .call(&Call {
                method: Method::GET,
                path: "/axis-cgi/wssession.cgi",
                query: &[],
                json: None,
                signed: true,
            })
            .await?;
        let token = String::from_utf8_lossy(&token).trim().to_owned();
        if token.is_empty() {
            return Err(protocol("the camera gave an empty session token"));
        }
        let (scheme, connector) = match camera.web.scheme {
            Scheme::Http => ("ws", Connector::Plain),
            Scheme::Https => ("wss", Connector::Rustls(Arc::new(tls::client_config()?))),
        };
        let mut url = Url::parse(&format!(
            "{scheme}://{}:{}/vapix/ws/v1",
            camera.host, camera.web.port
        ))
        .map_err(|_| {
            CameraError::new(
                ErrorKind::InvalidTarget,
                "the camera address is not a valid URL",
            )
        })?;
        url.query_pairs_mut()
            .append_pair("wssession", &token)
            .append_pair("apis", "data-hub");
        // The token is in this URL and nowhere else: no error below is built from the URL.
        let config = WebSocketConfig::default()
            .max_message_size(Some(LARGEST_MESSAGE))
            .max_frame_size(Some(LARGEST_MESSAGE));
        let handshake =
            connect_async_tls_with_config(url.as_str(), Some(config), false, Some(connector));
        let (stream, _) = match timeout(camera.timeout, handshake).await {
            Ok(result) => result.map_err(|error| transport(&error, false))?,
            Err(_) => {
                return Err(CameraError::new(
                    ErrorKind::TimedOut,
                    "no answer to the WebSocket upgrade in time",
                ));
            }
        };
        Ok(Self {
            stream,
            timeout: camera.timeout,
            requests: 0,
            next_ping: Instant::now() + HEARTBEAT,
            pong_due: None,
        })
    }

    /// Sends a request and returns its result. Samples that arrive meanwhile go to `early`. The camera's timeout
    /// covers the whole exchange, whatever else arrives meanwhile.
    pub(super) async fn call(
        &mut self,
        method: &str,
        params: &serde_json::Value,
        early: &mut Early,
    ) -> Result<serde_json::Value, CameraError> {
        self.requests += 1;
        let id = format!("ax-devil-{}", self.requests);
        let wait = self.timeout;
        let exchange = async {
            self.send(Message::text(protocol::request(&id, method, params)))
                .await?;
            loop {
                match self.receive().await? {
                    Incoming::Sample(sample) => {
                        if early.samples.len() == EARLY {
                            early.samples.pop_front();
                            early.dropped += 1;
                            warn!(
                                "DataHub samples arrived faster than the subscription was confirmed; the oldest was dropped"
                            );
                        }
                        early.samples.push_back(sample);
                    }
                    Incoming::Response(response) => {
                        return protocol::result(&response, &id, method);
                    }
                    Incoming::Ignored => {}
                }
            }
        };
        timeout(wait, exchange).await.unwrap_or_else(|_| {
            Err(CameraError::new(
                ErrorKind::TimedOut,
                format!("no answer to DataHub {method} in time"),
            ))
        })
    }

    /// Waits for the next sample, however long that takes.
    pub(super) async fn next_sample(&mut self) -> Result<Sample, CameraError> {
        loop {
            match self.receive().await? {
                Incoming::Sample(sample) => return Ok(sample),
                Incoming::Response(_) => {
                    return Err(protocol(
                        "the camera sent an answer to a request never made",
                    ));
                }
                Incoming::Ignored => {}
            }
        }
    }

    /// Closes politely, within a second; whether the camera answers does not matter.
    pub(super) async fn close(mut self) {
        let _ = timeout(CLOSE, self.stream.close(None)).await;
    }

    async fn send(&mut self, message: Message) -> Result<(), CameraError> {
        self.stream
            .send(message)
            .await
            .map_err(|error| transport(&error, true))
    }

    /// The next JSON message, keeping the connection alive meanwhile.
    async fn receive(&mut self) -> Result<Incoming, CameraError> {
        loop {
            enum Wake {
                Frame(Option<Result<Message, Error>>),
                Ping,
                Dead,
            }
            let wake = tokio::select! {
                frame = self.stream.next() => Wake::Frame(frame),
                () = sleep_until(self.next_ping) => Wake::Ping,
                () = due(self.pong_due) => Wake::Dead,
            };
            match wake {
                Wake::Frame(None) => return Err(lost("the camera closed the connection")),
                Wake::Frame(Some(Err(error))) => return Err(transport(&error, true)),
                Wake::Frame(Some(Ok(message))) => {
                    self.next_ping = Instant::now() + HEARTBEAT;
                    self.pong_due = None;
                    match message {
                        Message::Text(text) => return protocol::parse(text.as_str()),
                        Message::Ping(_) | Message::Pong(_) => {}
                        Message::Close(_) => return Err(lost("the camera closed the connection")),
                        Message::Binary(_) | Message::Frame(_) => {
                            return Err(protocol("the camera sent a frame that is not text"));
                        }
                    }
                }
                Wake::Ping => {
                    self.send(Message::Ping(Vec::new().into())).await?;
                    self.next_ping = Instant::now() + HEARTBEAT;
                    self.pong_due = Some(Instant::now() + HEARTBEAT / 2);
                }
                Wake::Dead => return Err(lost("the camera stopped answering pings")),
            }
        }
    }
}

/// Completes when `at` has passed; never when there is none.
async fn due(at: Option<Instant>) {
    match at {
        Some(at) => sleep_until(at).await,
        None => std::future::pending().await,
    }
}

fn lost(detail: &str) -> CameraError {
    CameraError::new(ErrorKind::Lost, detail)
}

/// The error for a failure of the connection, from the library's typed values, never from their text. `up` is whether
/// the upgrade had succeeded; before it, a transport failure means the camera could not be reached.
fn transport(error: &Error, up: bool) -> CameraError {
    let ended = if up {
        ErrorKind::Lost
    } else {
        ErrorKind::Unreachable
    };
    match error {
        // The answer to the upgrade was not 101: a redirect is an answer from elsewhere, a 401 a token not accepted.
        Error::Http(response) => {
            let status = response.status().as_u16();
            let kind = if status == 401 {
                ErrorKind::LoginRejected
            } else {
                ErrorKind::of_status(status)
            };
            CameraError::new(
                kind,
                format!("the WebSocket upgrade was answered with HTTP {status}"),
            )
        }
        Error::Io(io) => CameraError::new(ended, io.kind().to_string()),
        Error::Tls(_) => CameraError::new(ended, "the TLS connection failed"),
        // A camera that sends more than any sample is not speaking the protocol; another try would meet the same.
        Error::Capacity(_) => protocol("the camera sent a message larger than any DataHub sample"),
        Error::ConnectionClosed | Error::AlreadyClosed => lost("the camera closed the connection"),
        Error::Url(_) => CameraError::new(
            ErrorKind::InvalidTarget,
            "the camera address cannot be used",
        ),
        _ => CameraError::new(
            ErrorKind::Malformed,
            "the WebSocket connection broke its protocol",
        ),
    }
}
