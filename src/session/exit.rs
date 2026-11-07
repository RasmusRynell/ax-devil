//! How a Session socket ends: the close codes and what the peer is told.
//!
//! Close codes: 1000 the client left, 1007 not a valid message, 1008 not allowed, 1009 frame too big, 1013 full,
//! 4401 invalid password or link, 4404 session ended or unknown, 4409 seat replaced, 4429 too many commands. The
//! client treats 1007, 1008, 1009, 1013, 4401, 4404, 4409 and 4429 as final and reports them; any other close, or a
//! connection that drops without one, is retried with the seat's resume token. (A 4404 answering a resume of a seat
//! the server no longer holds is retried once with the way in the client entered by.)

use api_types::SessionServerMessage;
use axum::extract::ws::{CloseFrame, Message};
use futures_util::{Sink, SinkExt};
use sessions::EndReason;
use std::time::Duration;
use tokio::time::timeout;

pub(super) const INVALID_DATA: u16 = 1007;
pub(super) const NOT_ALLOWED: u16 = 1008;
pub(super) const TOO_BIG: u16 = 1009;
pub(super) const FULL: u16 = 1013;
pub(super) const INVALID_CREDENTIAL: u16 = 4401;
pub(super) const ENDED: u16 = 4404;
const REPLACED: u16 = 4409;
pub(super) const TOO_MANY_COMMANDS: u16 = 4429;

/// How long one send may take before the peer counts as gone.
pub(super) const SEND_DEADLINE: Duration = Duration::from_secs(10);

/// How a socket ends.
pub(super) enum Exit {
    /// The peer closed, vanished, stalled or went silent: nobody is left to tell.
    Gone,
    /// The client left.
    Left,
    /// Close with this code.
    Close(u16, String),
    /// The seat's session is over.
    Ended(EndReason),
}

impl Exit {
    pub(super) fn close(code: u16, reason: &str) -> Self {
        Self::Close(code, reason.to_owned())
    }
}

/// Sends `message` as text within [`SEND_DEADLINE`].
pub(super) async fn send<S: Sink<Message> + Unpin>(
    sink: &mut S,
    message: &SessionServerMessage,
) -> Result<(), Exit> {
    let text = serde_json::to_string(message).map_err(|error| {
        tracing::error!("Cannot encode a session message: {error}");
        Exit::Gone
    })?;
    send_frame(sink, Message::Text(text.into())).await
}

/// One send, which must finish within [`SEND_DEADLINE`]: a peer that stopped reading counts as gone.
pub(super) async fn send_frame<S: Sink<Message> + Unpin>(
    sink: &mut S,
    frame: Message,
) -> Result<(), Exit> {
    match timeout(SEND_DEADLINE, sink.send(frame)).await {
        Ok(Ok(())) => Ok(()),
        Ok(Err(_)) | Err(_) => Err(Exit::Gone),
    }
}

/// Tells the peer why the socket closes, as far as it will listen.
pub(super) async fn finish<S: Sink<Message> + Unpin>(sink: &mut S, exit: Exit) {
    let (code, mut reason) = match exit {
        Exit::Gone => return,
        Exit::Left => (1000, String::new()),
        Exit::Close(code, reason) => (code, reason),
        Exit::Ended(reason) => {
            let message = SessionServerMessage::Ended { reason };
            if send(sink, &message).await.is_err() {
                return;
            }
            let code = if reason == EndReason::Replaced {
                REPLACED
            } else {
                ENDED
            };
            (code, "the session is over".to_owned())
        }
    };
    // A close reason holds at most 123 bytes.
    let end = (0..=reason.len().min(120))
        .rev()
        .find(|end| reason.is_char_boundary(*end))
        .unwrap_or(0);
    reason.truncate(end);
    let frame = Message::Close(Some(CloseFrame {
        code,
        reason: reason.into(),
    }));
    // The peer may already be gone.
    let _ = send_frame(sink, frame).await;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::convert::Infallible;

    #[tokio::test(start_paused = true)]
    async fn a_send_that_never_finishes_counts_as_a_disconnect() {
        let mut stalled = Box::pin(futures_util::sink::unfold((), |(), _: Message| {
            std::future::pending::<Result<(), Infallible>>()
        }));
        let sent = send(
            &mut stalled,
            &SessionServerMessage::Heartbeat { server_us: 0 },
        )
        .await;
        assert!(matches!(sent, Err(Exit::Gone)));
    }
}
