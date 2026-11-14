//! The route that records a page's uncaught errors in the backend's log, where the log can say who sent them.

use crate::access::Principal;
use api_types::report::PageError;
use axum::{
    Json,
    extract::{ConnectInfo, DefaultBodyLimit},
    http::{HeaderMap, StatusCode, header::USER_AGENT},
};
use std::{
    net::SocketAddr,
    sync::{Mutex, PoisonError},
    time::{Duration, Instant},
};

/// How many errors are logged in each [`PERIOD`] for each kind of caller; the rest are answered 429. Callers without a
/// credential have a budget of their own, so they cannot use up the others'.
const PER_PERIOD: u32 = 30;
const PERIOD: Duration = Duration::from_secs(60);

/// Counts the errors logged in the current period.
struct Budget(Mutex<Option<(Instant, u32)>>);

impl Budget {
    /// Whether one more error may be logged at `now`.
    fn admit(&self, now: Instant) -> bool {
        let mut logged = self.0.lock().unwrap_or_else(PoisonError::into_inner);
        match &mut *logged {
            Some((start, count)) if now.duration_since(*start) < PERIOD => {
                *count += 1;
                *count <= PER_PERIOD
            }
            slot => {
                *slot = Some((now, 1));
                true
            }
        }
    }
}

static FULL: Budget = Budget(Mutex::new(None));
static OWNER: Budget = Budget(Mutex::new(None));
static ANYONE: Budget = Budget(Mutex::new(None));

/// The largest body accepted; a stack trace is a few kilobytes.
pub(crate) const BODY_LIMIT: usize = 16 * 1024;
/// The longest text kept of any one field.
const CLIP: usize = 8 * 1024;

/// The body limit as a layer, for the route table.
pub(crate) fn limit() -> DefaultBodyLimit {
    DefaultBodyLimit::max(BODY_LIMIT)
}

fn clip(text: &str) -> &str {
    let end = text
        .char_indices()
        .nth(CLIP)
        .map_or(text.len(), |(index, _)| index);
    &text[..end]
}

/// Logs the error with who sent it: the credential it held, its address and its browser. Text goes through `Debug`,
/// so a page cannot add log lines of its own, and a flood is cut off at [`PER_PERIOD`] a minute per kind of caller.
pub(crate) async fn post(
    principal: Principal,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    Json(error): Json<PageError>,
) -> StatusCode {
    let (caller, budget) = match principal {
        Principal::Owner { full: true } => ("owner with full access", &FULL),
        Principal::Owner { full: false } => ("owner", &OWNER),
        Principal::None => ("no credential", &ANYONE),
    };
    if !budget.admit(Instant::now()) {
        return StatusCode::TOO_MANY_REQUESTS;
    }
    let agent = headers
        .get(USER_AGENT)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    tracing::error!(
        caller,
        from = %peer.ip(),
        page = ?clip(&error.page),
        desktop = error.desktop,
        agent = ?clip(agent),
        stack = ?clip(&error.details),
        "Uncaught error in a page: {:?}",
        clip(&error.summary)
    );
    StatusCode::NO_CONTENT
}

#[cfg(test)]
mod tests {
    use super::{BODY_LIMIT, Budget, PER_PERIOD, PERIOD};
    use crate::test_support::TestApp;
    use axum::{
        body::Body,
        extract::ConnectInfo,
        http::{Request, StatusCode},
    };
    use std::{net::SocketAddr, sync::Mutex, time::Instant};

    #[test]
    fn a_flood_is_cut_off_until_the_next_period() {
        let budget = Budget(Mutex::new(None));
        let start = Instant::now();
        assert!((0..PER_PERIOD).all(|_| budget.admit(start)));
        assert!(!budget.admit(start));
        assert!(budget.admit(start + PERIOD));
    }

    async fn post(body: String) -> StatusCode {
        let mut request = Request::builder()
            .method("POST")
            .uri("/api/client-errors")
            .header("content-type", "application/json")
            .body(Body::from(body))
            .unwrap();
        request
            .extensions_mut()
            .insert(ConnectInfo(SocketAddr::from(([10, 0, 0, 9], 4000))));
        TestApp::new().send(request).await.0
    }

    #[tokio::test]
    async fn a_page_error_is_taken_and_an_oversized_one_is_refused() {
        let body = |details: &str| {
            format!(
                r#"{{"summary":"Error: x","details":"{details}","page":"http://127.0.0.1/","desktop":false}}"#
            )
        };
        assert_eq!(post(body("at a")).await, StatusCode::NO_CONTENT);
        assert_eq!(
            post(body(&"x".repeat(BODY_LIMIT))).await,
            StatusCode::PAYLOAD_TOO_LARGE
        );
    }
}
