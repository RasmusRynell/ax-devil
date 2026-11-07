//! A refused keepalive ends the stream as a login failure. Retina only logs it and carries on, so the log record
//! is caught: `retina_log_layer` raises a flag that is delivered through a thread-local scoped to one synchronous
//! poll of the session (retina's keepalive runs only inside that poll), so one stream's refusal never reaches another.

use std::cell::Cell;
use std::future::poll_fn;
use std::task::Poll;

use futures_util::StreamExt;
use retina::client::{PacketItem, Playing, Session};
use tracing::field::{Field, Visit};
use tracing::{Event, Level, Subscriber};
use tracing_log::NormalizeEvent;
use tracing_subscriber::Layer;
use tracing_subscriber::layer::Context;

use crate::error::{CameraError, ErrorKind};

/// The target retina's `log` record carries; the `log` bridge reports it as `log`.
const RETINA_CLIENT: &str = "retina::client";

thread_local! {
    /// `Some` inside a watched poll: whether a refusal was logged during it.
    static REFUSED: Cell<Option<bool>> = const { Cell::new(None) };
}

/// A layer that notices retina refusing a keepalive with 401 or 403 (a revoked login), so a stream that is playing
/// ends as `LoginRejected` instead of waiting for the silence watchdog. **The process must install it** in its
/// `tracing` subscriber, and that subscriber's `init` must install the `log` bridge, as retina logs through the `log`
/// crate; without both, `open_stream` still works but a refused keepalive goes unnoticed. A
/// refusal with another status (405 for a camera that does not know the method, a 5xx) does not end the stream.
pub fn retina_log_layer<S: Subscriber>() -> impl Layer<S> {
    KeepaliveLayer
}

struct KeepaliveLayer;

impl<S: Subscriber> Layer<S> for KeepaliveLayer {
    fn on_event(&self, event: &Event<'_>, _: Context<'_, S>) {
        let metadata = event.normalized_metadata();
        let metadata = metadata.as_ref().unwrap_or_else(|| event.metadata());
        if *metadata.level() != Level::WARN || !metadata.target().starts_with(RETINA_CLIENT) {
            return;
        }
        let mut message = Message(String::new());
        event.record(&mut message);
        if refuses_login(&message.0) {
            REFUSED.with(|refused| {
                if refused.get().is_some() {
                    refused.set(Some(true));
                }
            });
        }
    }
}

struct Message(String);

impl Visit for Message {
    fn record_debug(&mut self, field: &Field, value: &dyn std::fmt::Debug) {
        if field.name() == "message" {
            self.0 = format!("{value:?}");
        }
    }
}

/// Whether retina's warning (`keepalive failed with {:?}` of its `StatusCode`, which debug-prints as `StatusCode(401)`)
/// says the camera no longer accepts the login.
fn refuses_login(message: &str) -> bool {
    message
        .strip_prefix("keepalive failed with StatusCode(")
        .and_then(|status| status.strip_suffix(')'))
        .is_some_and(|status| matches!(status, "401" | "403"))
}

/// The next item of a playing session; `Err` as soon as a poll of it logged a keepalive refusal of the login.
pub(super) async fn next(
    session: &mut Session<Playing>,
) -> Result<Option<Result<PacketItem, retina::Error>>, CameraError> {
    poll_fn(|cx| {
        let outer = REFUSED.replace(Some(false));
        let poll = session.poll_next_unpin(cx);
        let refused = REFUSED.replace(outer) == Some(true);
        if refused {
            return Poll::Ready(Err(CameraError::new(
                ErrorKind::LoginRejected,
                "the camera refused the keepalive login",
            )));
        }
        poll.map(Ok)
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::refuses_login;

    #[test]
    fn only_a_refused_login_is_one() {
        for (message, refused) in [
            ("keepalive failed with StatusCode(401)", true),
            ("keepalive failed with StatusCode(403)", true),
            ("keepalive failed with StatusCode(405)", false),
            ("keepalive failed with StatusCode(500)", false),
            ("keepalive failed with StatusCode(4010)", false),
        ] {
            assert_eq!(refuses_login(message), refused, "{message}");
        }
    }
}
