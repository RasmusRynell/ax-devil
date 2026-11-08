//! A refused keepalive ends the stream as a login failure. Retina only logs it and carries on, so the log record
//! is caught: `retina_log_layer` raises a flag that is delivered through a thread-local scoped to one synchronous
//! poll of the session (retina's keepalive runs only inside that poll), so one stream's refusal never reaches another.

use std::cell::Cell;
use std::future::poll_fn;
use std::task::Poll;

use futures_util::StreamExt;
use retina::client::{PacketItem, Playing, Session};
use tracing::field::{Field, Visit};
use tracing::level_filters::LevelFilter;
use tracing::{Event, Level, Subscriber};
use tracing_subscriber::filter::filter_fn;
use tracing_subscriber::layer::{Context, Layer};
use tracing_subscriber::registry::LookupSpan;

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
///
/// It is scoped by a per-layer filter to warnings from retina's client (the `log` bridge asks the subscriber with the
/// record's real target, so bridged records match), so it enables no other callsite. It contributes a maximum level of
/// warnings to the process's; the process's maximum must be at least that, as the `log` bridge takes its own from it,
/// including when its output is set to errors only. The subscriber must not hold an unfiltered layer, which would
/// enable everything.
pub fn retina_log_layer<S>() -> impl Layer<S>
where
    S: Subscriber + for<'a> LookupSpan<'a>,
{
    KeepaliveLayer.with_filter(
        filter_fn(|metadata| {
            *metadata.level() == Level::WARN && metadata.target().starts_with(RETINA_CLIENT)
        })
        .with_max_level_hint(LevelFilter::WARN),
    )
}

struct KeepaliveLayer;

impl<S: Subscriber> Layer<S> for KeepaliveLayer {
    fn on_event(&self, event: &Event<'_>, _: Context<'_, S>) {
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
    use super::{refuses_login, retina_log_layer};
    use tracing::level_filters::LevelFilter;
    use tracing::{Level, Subscriber};
    use tracing_subscriber::layer::SubscriberExt;

    /// A layer in every process must not make other code's debug and trace callsites enabled, nor raise the level the
    /// `log` bridge takes from the subscriber (the app has many of those in its decode and paint paths).
    #[test]
    fn the_layer_enables_nothing_but_retinas_warnings() {
        let subscriber = tracing_subscriber::registry().with(retina_log_layer());
        assert_eq!(subscriber.max_level_hint(), Some(LevelFilter::WARN));
        tracing::subscriber::with_default(subscriber, || {
            assert!(!tracing::enabled!(Level::DEBUG));
            assert!(!tracing::enabled!(Level::WARN));
            assert!(!tracing::enabled!(target: "retina::client", Level::DEBUG));
            assert!(tracing::enabled!(target: "retina::client", Level::WARN));
        });
    }

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
