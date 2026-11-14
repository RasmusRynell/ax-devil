//! The socket to the page: accepting its one connection, checking its `Hello`, and reading and writing messages.

use super::{
    Graphics, RenderError,
    page::{Handshake, Page, Spawn},
};
use api_types::render::{PageMessage, RENDER_PROTOCOL};
use foundation::CancelToken;
use sessions::Secret;
use std::{
    fmt,
    io::{self, ErrorKind, Read, Write},
    net::{Ipv4Addr, TcpListener, TcpStream},
    thread,
    time::{Duration, Instant},
};
use tungstenite::{HandshakeError, Message, WebSocket, protocol::WebSocketConfig};

/// How long the page process gets to start and connect.
const START_TIMEOUT: Duration = Duration::from_secs(30);
/// How long a connection gets, in all, to complete the WebSocket handshake and say `Hello`.
const HELLO_TIMEOUT: Duration = Duration::from_secs(5);
/// The longest `Hello` the host reads.
const HELLO_LIMIT: usize = 4096;
/// How long the page gets to answer a `Setup`, which includes starting the graphics.
pub(super) const SETUP_TIMEOUT: Duration = Duration::from_secs(120);
/// How long the page gets to answer a `Render`, and to take a message the host sends. Pings and pongs do not extend it.
pub(super) const FRAME_TIMEOUT: Duration = Duration::from_secs(60);
/// How often a wait on the page looks at the cancel token: reads and writes on the socket time out this often.
const CANCEL_POLL: Duration = Duration::from_millis(100);
/// How often the accept loop looks for a connection and for a dead process.
const ACCEPT_POLL: Duration = Duration::from_millis(20);

/// The page's stream. Once `stop_at` has passed or the job is cancelled, reads and writes fail as timed out, even
/// when bytes keep arriving: tungstenite reads on until the socket would block, so a peer that trickles bytes
/// would otherwise keep it inside one call, out of reach of the deadline and the cancel token.
pub(super) struct Guarded {
    stream: TcpStream,
    stop_at: Instant,
    cancel: CancelToken,
}

impl Guarded {
    fn check(&self) -> io::Result<()> {
        if self.cancel.is_cancelled() || Instant::now() >= self.stop_at {
            Err(ErrorKind::TimedOut.into())
        } else {
            Ok(())
        }
    }
}

/// A timeout of the underlying socket, as the `WouldBlock` that tungstenite resumes after; only [`Guarded`] reports
/// the end of the wait as `TimedOut`.
fn resumable<T>(result: io::Result<T>) -> io::Result<T> {
    result.map_err(|error| {
        if is_timeout(&error) {
            ErrorKind::WouldBlock.into()
        } else {
            error
        }
    })
}

impl Read for Guarded {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        self.check()?;
        resumable(self.stream.read(buf))
    }
}

impl Write for Guarded {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.check()?;
        resumable(self.stream.write(buf))
    }

    fn flush(&mut self) -> io::Result<()> {
        self.check()?;
        resumable(self.stream.flush())
    }
}

/// A page process and the socket to it.
pub(super) struct Link {
    pub(super) socket: WebSocket<Guarded>,
    pub(super) page: Page,
    /// Every wait on the page ends, with an error, soon after this is cancelled.
    pub(super) cancel: CancelToken,
}

impl Link {
    /// Writes `message` and flushes it, within [`FRAME_TIMEOUT`].
    pub(super) fn send(&mut self, message: Message) -> Result<(), RenderError> {
        let deadline = Instant::now() + FRAME_TIMEOUT;
        self.socket.get_mut().stop_at = deadline;
        // A write that would block queues the message; flushing finishes it.
        match self.socket.write(message) {
            Ok(()) => {}
            Err(tungstenite::Error::Io(io)) if is_timeout(&io) => {}
            Err(error) => return Err(self.lost(&error)),
        }
        loop {
            match self.socket.flush() {
                Ok(()) => return Ok(()),
                Err(tungstenite::Error::Io(io)) if is_timeout(&io) => {
                    self.keep_waiting(deadline, FRAME_TIMEOUT, "take a message")?;
                }
                Err(error) => return Err(self.lost(&error)),
            }
        }
    }

    /// The next data message, skipping pings and pongs, which come within `within` of this call.
    pub(super) fn next(&mut self, within: Duration) -> Result<Message, RenderError> {
        let deadline = Instant::now() + within;
        self.socket.get_mut().stop_at = deadline;
        loop {
            self.keep_waiting(deadline, within, "answer")?;
            match self.socket.read() {
                Ok(message @ (Message::Binary(_) | Message::Text(_))) => return Ok(message),
                Ok(Message::Close(_)) => {
                    return Err(self.lost(&tungstenite::Error::ConnectionClosed));
                }
                Ok(_) => {}
                Err(tungstenite::Error::Io(io)) if is_timeout(&io) => {}
                Err(error) => return Err(self.lost(&error)),
            }
        }
    }

    /// Fails when the job is cancelled or `deadline`, which is `within` after the wait began, has passed.
    fn keep_waiting(
        &mut self,
        deadline: Instant,
        within: Duration,
        action: &str,
    ) -> Result<(), RenderError> {
        if self.cancel.is_cancelled() {
            return Err(RenderError::new("the render job was cancelled"));
        }
        if Instant::now() < deadline {
            return Ok(());
        }
        let said = self.page.exited().unwrap_or_else(|| self.page.said());
        Err(RenderError::new(format!(
            "the render page did not {action} within {} seconds{said}",
            within.as_secs()
        )))
    }

    /// The next message, which must be text from the page, within `within`.
    pub(super) fn page_message(&mut self, within: Duration) -> Result<PageMessage, RenderError> {
        match self.next(within)? {
            Message::Text(text) => serde_json::from_str(&text).map_err(|error| {
                RenderError::new(format!(
                    "the render page sent a message the host cannot read: {error}"
                ))
            }),
            _ => Err(RenderError::new(
                "the render page sent a picture before the job was set up",
            )),
        }
    }

    /// The error for a socket that failed with `error`, with what the page process said before it did.
    fn lost(&mut self, error: &tungstenite::Error) -> RenderError {
        let what = match error {
            tungstenite::Error::ConnectionClosed | tungstenite::Error::AlreadyClosed => {
                "the render page closed the connection".to_owned()
            }
            tungstenite::Error::Capacity(_) => {
                "the render page sent a message larger than the output".to_owned()
            }
            other => format!("the connection to the render page failed: {other}"),
        };
        let said = self.page.exited().unwrap_or_else(|| self.page.said());
        RenderError::new(format!("{what}{said}"))
    }
}

/// Whether a socket read or write ended because its timeout passed.
fn is_timeout(error: &std::io::Error) -> bool {
    matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut)
}

/// Starts a page process with `graphics` and accepts its connection. Gives up, and ends the page, once `cancel` is
/// cancelled.
pub(super) fn connect(
    spawn: &mut Spawn,
    graphics: Graphics,
    cancel: &CancelToken,
) -> Result<Link, RenderError> {
    let io_error = |error: std::io::Error| {
        RenderError::new(format!("Cannot listen for the render page: {error}"))
    };
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).map_err(io_error)?;
    listener.set_nonblocking(true).map_err(io_error)?;
    let port = listener.local_addr().map_err(io_error)?.port();
    let secret = Secret::generate();
    let mut page = spawn(
        graphics,
        &Handshake {
            port,
            secret: secret.to_string(),
        },
    )?;
    let deadline = Instant::now() + START_TIMEOUT;
    loop {
        // Checked on every pass, so connections that keep arriving cannot hold off the end of the wait.
        if cancel.is_cancelled() {
            return Err(RenderError::new("the render job was cancelled"));
        }
        if let Some(ended) = page.exited() {
            return Err(RenderError::new(format!(
                "Cannot start the render page: {ended}"
            )));
        }
        if Instant::now() >= deadline {
            return Err(RenderError::new(format!(
                "the render page did not connect within {} seconds{}",
                START_TIMEOUT.as_secs(),
                page.said()
            )));
        }
        match listener.accept() {
            Ok((stream, _)) => {
                let until = deadline.min(Instant::now() + HELLO_TIMEOUT);
                match greet(stream, &secret, until, cancel) {
                    Greeting::Page(socket) => {
                        return Ok(Link {
                            socket: *socket,
                            page,
                            cancel: cancel.clone(),
                        });
                    }
                    Greeting::Stranger(why) => {
                        tracing::warn!("Refused a connection to the render page's port: {why}");
                    }
                    Greeting::Mismatch(protocol) => {
                        return Err(RenderError::new(format!(
                            "the render page speaks protocol {protocol} but this build speaks {RENDER_PROTOCOL}; rebuild the frontend"
                        )));
                    }
                }
            }
            Err(error) if error.kind() == ErrorKind::WouldBlock => thread::sleep(ACCEPT_POLL),
            Err(error) => return Err(io_error(error)),
        }
    }
}

/// What a connection to the page's port turned out to be.
enum Greeting {
    /// The page: it sent `Hello` with the secret and our protocol.
    Page(Box<WebSocket<Guarded>>),
    /// Anything else that connected; ignored, so another program cannot stop the job by knocking.
    Stranger(String),
    /// The page, but built for another protocol.
    Mismatch(u32),
}

/// Completes the WebSocket handshake on `stream` and reads the `Hello`, all by `until`. Reads and writes time out
/// every [`CANCEL_POLL`] and the stream stops at `until` or on `cancel`, so a connection that trickles bytes still
/// ends then; both end it as a stranger, and the caller's own checks then end the job.
fn greet(stream: TcpStream, secret: &Secret, until: Instant, cancel: &CancelToken) -> Greeting {
    let stranger = |why: &dyn fmt::Display| Greeting::Stranger(why.to_string());
    if let Err(error) = stream
        .set_nonblocking(false)
        .and_then(|()| stream.set_nodelay(true))
        .and_then(|()| stream.set_read_timeout(Some(CANCEL_POLL)))
        .and_then(|()| stream.set_write_timeout(Some(CANCEL_POLL)))
    {
        return stranger(&error);
    }
    let stream = Guarded {
        stream,
        stop_at: until,
        cancel: cancel.clone(),
    };
    let stopped = || {
        if cancel.is_cancelled() {
            Some("the job was cancelled")
        } else if Instant::now() >= until {
            Some("it did not finish its Hello in time")
        } else {
            None
        }
    };
    let config = WebSocketConfig::default()
        .max_message_size(Some(HELLO_LIMIT))
        .max_frame_size(Some(HELLO_LIMIT));
    let mut attempt = tungstenite::accept_with_config(stream, Some(config));
    let mut socket = loop {
        match attempt {
            Ok(socket) => break socket,
            Err(HandshakeError::Interrupted(mid)) => {
                if let Some(why) = stopped() {
                    return stranger(&why);
                }
                attempt = mid.handshake();
            }
            Err(HandshakeError::Failure(error)) => return stranger(&error),
        }
    };
    let message = loop {
        match socket.read() {
            Ok(message) => break message,
            Err(tungstenite::Error::Io(io)) if is_timeout(&io) => {
                if let Some(why) = stopped() {
                    return stranger(&why);
                }
            }
            Err(error) => return stranger(&error),
        }
    };
    match message {
        Message::Text(text) => match serde_json::from_str(&text) {
            Ok(PageMessage::Hello {
                protocol,
                secret: given,
            }) if secret.matches(&given) => {
                if protocol == RENDER_PROTOCOL {
                    Greeting::Page(Box::new(socket))
                } else {
                    Greeting::Mismatch(protocol)
                }
            }
            Ok(_) => stranger(&"the first message was not a Hello with the secret"),
            Err(error) => stranger(&error),
        },
        _ => stranger(&"the first message was not text"),
    }
}
