//! Who is asking and what they may do: principals, the gate of each route, the guard that keeps other websites off the Local
//! listener, the headers every response carries, and the limits on open sockets.

use api_types::ApiError;
use axum::{
    Json,
    extract::{FromRef, FromRequestParts, Request, State},
    http::{
        HeaderMap, HeaderName, HeaderValue, StatusCode, header, request::Parts, uri::Authority,
    },
    middleware::Next,
    response::{IntoResponse, Response},
};
use serde_json::Value;
use sessions::Secret;
use settings::Settings;
use std::{
    collections::HashMap,
    convert::Infallible,
    fmt,
    net::IpAddr,
    sync::{Arc, Mutex, PoisonError},
};

/// How a listener decides who is asking.
pub enum Policy {
    /// The desktop and development listener on loopback: whoever reaches it is the person at this machine, provided
    /// the request passes [`local_guard`].
    Local,
    /// A listener other machines reach. Nothing is granted by address or cookie: the server password, sent as a
    /// bearer token, is the only way to be more than [`Principal::None`].
    Shared {
        /// The server password; without one nobody is an Owner here.
        password: Option<Secret>,
        /// Read on every request for `server.password_full_access`, which decides whether a holder of the password has
        /// Full access. A Session keeps the `expands` it was created with when the setting changes.
        settings: Arc<Settings>,
    },
}

/// Who a request comes from, which decides what it may do.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Principal {
    /// The Owner: the person at this machine, or a holder of the server password. `full` is Full access.
    Owner {
        /// Whether the Owner may reach outside ax-devil on the server's machine.
        full: bool,
    },
    /// No credential.
    None,
}

impl Principal {
    /// Whether this is the Owner.
    pub fn is_owner(self) -> bool {
        matches!(self, Self::Owner { .. })
    }

    /// Whether this is an Owner with Full access.
    pub fn full_access(self) -> bool {
        matches!(self, Self::Owner { full: true })
    }
}

impl Policy {
    fn principal(&self, headers: &HeaderMap) -> Principal {
        self.principal_for(bearer(headers))
    }

    /// Who holds `password`, which a browser socket sends in its first message because it cannot set headers.
    pub fn principal_for(&self, password: Option<&str>) -> Principal {
        match self {
            Self::Local => Principal::Owner { full: true },
            Self::Shared {
                password: Some(secret),
                settings,
            } if password.is_some_and(|candidate| secret.matches(candidate)) => Principal::Owner {
                full: settings.state().resolved("server.password_full_access")
                    == Some(Value::Bool(true)),
            },
            Self::Shared { .. } => Principal::None,
        }
    }

    /// Whether this is the Local listener, whose Owner is the person at this machine. Only the command-line launch
    /// hand-off asks; permissions are always the [`Principal`].
    pub fn is_local(&self) -> bool {
        matches!(self, Self::Local)
    }
}

fn bearer(headers: &HeaderMap) -> Option<&str> {
    let value = headers.get(header::AUTHORIZATION)?.to_str().ok()?;
    let (scheme, token) = value.split_once(' ')?;
    scheme.eq_ignore_ascii_case("bearer").then_some(token)
}

impl<S: Send + Sync> FromRequestParts<S> for Principal
where
    Arc<Policy>: FromRef<S>,
{
    type Rejection = Infallible;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Infallible> {
        Ok(Arc::<Policy>::from_ref(state).principal(&parts.headers))
    }
}

/// Who a route lets through, as the route table states it. Everything beyond `Principal` checks stays in the handler.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Who {
    /// Anyone; the handler may show more to an Owner.
    Public,
    /// The Owner.
    Owner,
    /// An Owner with Full access.
    FullAccess,
    /// A WebSocket upgrade: the socket limits admit it and the first message authenticates it: `Hello` on the
    /// Session socket, a ticket on the playback socket.
    Upgrade,
}

impl Who {
    /// The status that refuses `principal` at this gate, or `None` when it passes: 401 for a caller that is not an
    /// Owner, 403 for an Owner without the Full access the route needs.
    pub fn refusal(self, principal: Principal) -> Option<StatusCode> {
        match self {
            Self::Public | Self::Upgrade => None,
            Self::Owner | Self::FullAccess if !principal.is_owner() => {
                Some(StatusCode::UNAUTHORIZED)
            }
            Self::FullAccess if !principal.full_access() => Some(StatusCode::FORBIDDEN),
            Self::Owner | Self::FullAccess => None,
        }
    }
}

/// Answers 401 or 403 unless the caller passes the gate `who` of the route.
pub async fn require(
    State((policy, who)): State<(Arc<Policy>, Who)>,
    request: Request,
    next: Next,
) -> Response {
    match who.refusal(policy.principal(request.headers())) {
        None => next.run(request).await,
        Some(StatusCode::FORBIDDEN) => refuse(
            StatusCode::FORBIDDEN,
            "this needs full access",
            "authorization",
        ),
        Some(status) => refuse(status, "this needs the server password", "authorization"),
    }
}

/// Keeps other websites off the Local listener. A page reached through a rebound DNS name has the same `Origin` and
/// `Host` as the real one, so `Host` must be a loopback name; a page from another site sends a foreign `Origin`, compared in full (scheme, host and port).
/// Requests without `Origin` (the desktop shell, scripts, plain navigation) pass.
pub async fn local_guard(request: Request, next: Next) -> Response {
    if is_local_page(request.headers()) {
        next.run(request).await
    } else {
        refuse(
            StatusCode::FORBIDDEN,
            "this backend only answers pages it served itself",
            "origin",
        )
    }
}

fn is_local_page(headers: &HeaderMap) -> bool {
    let Some(host) = headers
        .get(header::HOST)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<Authority>().ok())
    else {
        return false;
    };
    let loopback = matches!(
        host.host().to_ascii_lowercase().as_str(),
        "127.0.0.1" | "localhost" | "[::1]"
    );
    // The Local listener speaks plain HTTP, so a page of it has exactly this origin; any other scheme, host or port is
    // another site.
    let same_origin = headers.get(header::ORIGIN).is_none_or(|origin| {
        origin
            .to_str()
            .is_ok_and(|origin| origin.eq_ignore_ascii_case(&format!("http://{host}")))
    });
    loopback && same_origin
}

/// What the embedded frontend needs and nothing more: its own origin for scripts, styles and its socket, and no
/// framing by any page. `data:` images are allowed because Vite inlines small icons as `data:` SVGs used as CSS masks.
const CONTENT_SECURITY_POLICY: &str =
    "frame-ancestors 'none'; default-src 'self'; img-src 'self' data:";

/// Adds the headers that keep any page from framing this backend and any injected script from loading code from
/// elsewhere. Every response on every listener carries them, refusals included.
pub async fn security_headers(request: Request, next: Next) -> Response {
    let mut response = next.run(request).await;
    let headers = response.headers_mut();
    headers.insert(
        header::CONTENT_SECURITY_POLICY,
        HeaderValue::from_static(CONTENT_SECURITY_POLICY),
    );
    headers.insert(
        HeaderName::from_static("x-frame-options"),
        HeaderValue::from_static("DENY"),
    );
    response
}

fn refuse(status: StatusCode, reason: &str, location: &str) -> Response {
    (
        status,
        Json(ApiError {
            reason: reason.to_owned(),
            location: location.to_owned(),
        }),
    )
        .into_response()
}

/// How many sockets one server keeps open, in total and per peer address.
#[derive(Clone, Copy)]
pub struct Limits {
    /// Open sockets, counting those still waiting for their first message.
    pub sockets: usize,
    /// Sockets that have not yet sent their first message.
    pub pending: usize,
    /// Open sockets from one peer address.
    pub peer_sockets: usize,
    /// Sockets from one peer address that have not yet sent their first message.
    pub peer_pending: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            // A viewer holds a session socket and a playback socket per pane: room for a few hundred viewers.
            sockets: 1024,
            pending: 64,
            peer_sockets: 16,
            peer_pending: 4,
        }
    }
}

impl Limits {
    /// For a listener whose clients all share one address, such as a server behind a reverse proxy or the Local
    /// listener: a peer may use as many sockets as the whole server, so only the totals bind.
    pub fn behind_proxy() -> Self {
        let totals = Self::default();
        Self {
            peer_sockets: totals.sockets,
            peer_pending: totals.pending,
            ..totals
        }
    }
}

/// Why a socket was not admitted.
#[derive(Debug, PartialEq, Eq)]
pub enum Refused {
    /// The server as a whole is at a limit.
    Busy,
    /// This peer address is at a limit.
    PeerBusy,
}

impl fmt::Display for Refused {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str(match self {
            Self::Busy => "the server has too many open connections",
            Self::PeerBusy => "too many open connections from this address",
        })
    }
}

#[derive(Default, Clone, Copy)]
struct Usage {
    sockets: usize,
    pending: usize,
}

#[derive(Default)]
struct Counts {
    all: Usage,
    peers: HashMap<IpAddr, Usage>,
}

/// Counts open sockets and sockets that have not said hello, so one peer or one crowd cannot use up the server.
pub struct Admission {
    limits: Limits,
    counts: Mutex<Counts>,
}

impl Admission {
    /// A gate with these limits.
    pub fn new(limits: Limits) -> Self {
        Self {
            limits,
            counts: Mutex::new(Counts::default()),
        }
    }

    /// Admits a socket from `peer`, counted as pending until [`Slot::hello_received`], or says which limit stops it.
    /// The slot is released when dropped.
    pub fn admit(self: &Arc<Self>, peer: IpAddr) -> Result<Slot, Refused> {
        let mut counts = self.counts.lock().unwrap_or_else(PoisonError::into_inner);
        let mine = counts.peers.get(&peer).copied().unwrap_or_default();
        let limits = self.limits;
        if counts.all.sockets >= limits.sockets || counts.all.pending >= limits.pending {
            return Err(Refused::Busy);
        }
        if mine.sockets >= limits.peer_sockets || mine.pending >= limits.peer_pending {
            return Err(Refused::PeerBusy);
        }
        counts.all.sockets += 1;
        counts.all.pending += 1;
        let usage = counts.peers.entry(peer).or_default();
        usage.sockets += 1;
        usage.pending += 1;
        Ok(Slot {
            admission: Arc::clone(self),
            peer,
            pending: true,
        })
    }

    fn release(&self, peer: IpAddr, pending: bool) {
        let mut counts = self.counts.lock().unwrap_or_else(PoisonError::into_inner);
        let drop_one = |usage: &mut Usage| {
            usage.sockets -= 1;
            usage.pending -= usize::from(pending);
        };
        drop_one(&mut counts.all);
        if let Some(usage) = counts.peers.get_mut(&peer) {
            drop_one(usage);
            if usage.sockets == 0 {
                counts.peers.remove(&peer);
            }
        }
    }
}

/// One admitted socket; dropping it frees its place.
pub struct Slot {
    admission: Arc<Admission>,
    peer: IpAddr,
    pending: bool,
}

impl Slot {
    /// The socket sent its first message, so it no longer counts as pending.
    pub fn hello_received(&mut self) {
        if self.pending {
            self.pending = false;
            let mut counts = self
                .admission
                .counts
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            counts.all.pending -= 1;
            if let Some(usage) = counts.peers.get_mut(&self.peer) {
                usage.pending -= 1;
            }
        }
    }
}

impl Drop for Slot {
    fn drop(&mut self) {
        self.admission.release(self.peer, self.pending);
    }
}
