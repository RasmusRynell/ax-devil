//! Where other people reach this backend: the base of the links it hands out, the listener the desktop opens for
//! them while a session is shared, and the rule that only a build with the embedded frontend can be reached by a
//! browser from another machine.

use crate::{AppState, access::Policy, lan_app};
use ::sessions::JoinLink;
use axum::http::Uri;
use std::{
    fmt,
    net::{IpAddr, Ipv4Addr, SocketAddr, UdpSocket},
    sync::{Arc, Mutex, PoisonError},
    time::Duration,
};
use tokio::{sync::Mutex as AsyncMutex, task::JoinHandle};

/// The address other machines use to reach a listener, such as `http://192.168.1.20:7340`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LinkBase(String);

impl LinkBase {
    /// The `--public-url` the operator gave: an `http` or `https` origin, which a reverse proxy may own.
    pub fn parse_public(text: &str) -> Result<Self, String> {
        let uri = text
            .parse::<Uri>()
            .map_err(|error| format!("not a URL: {error}"))?;
        let origin_only = uri.path() == "/" && uri.query().is_none();
        match (uri.scheme_str(), uri.authority()) {
            (Some("http" | "https"), Some(authority)) if origin_only => Ok(Self(format!(
                "{}://{authority}",
                uri.scheme_str().unwrap_or("http")
            ))),
            _ => Err("expected an origin such as https://share.example.com".into()),
        }
    }

    /// The links for a listener bound to `bound`: the `public` URL if there is one, else the address the listener is
    /// bound to, or this machine's LAN address when it is bound to every interface.
    pub fn for_listener(public: Option<Self>, bound: SocketAddr) -> Self {
        public.unwrap_or_else(|| {
            let ip = if bound.ip().is_unspecified() {
                lan_ip().unwrap_or(Ipv4Addr::LOCALHOST.into())
            } else {
                bound.ip()
            };
            Self(format!("http://{}", SocketAddr::new(ip, bound.port())))
        })
    }

    /// Whether links to this base are encrypted on the way.
    pub fn is_https(&self) -> bool {
        self.0.starts_with("https://")
    }

    /// The base with a secret in the fragment, which browsers never send to a server or a proxy.
    pub fn link(&self, fragment: &str) -> String {
        format!("{}/#{fragment}", self.0)
    }

    /// The base without a path or fragment.
    pub fn url(&self) -> &str {
        &self.0
    }
}

/// This machine's address on the network that reaches the internet. A UDP `connect` only picks the route, so nothing
/// is sent.
fn lan_ip() -> Option<IpAddr> {
    let socket = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0)).ok()?;
    socket.connect((Ipv4Addr::new(8, 8, 8, 8), 80)).ok()?;
    socket.local_addr().ok().map(|address| address.ip())
}

/// Development builds serve no frontend, so a browser on another machine would get nothing from them.
pub fn refuse_development_build() -> Result<(), String> {
    if cfg!(feature = "embed-assets") {
        Ok(())
    } else {
        Err(
            "this development build serves no frontend; run `make build` for the release build"
                .into(),
        )
    }
}

/// Why the listener for other machines could not be opened.
#[derive(Debug)]
pub struct ShareError(String);

impl fmt::Display for ShareError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ShareError {}

/// How often the desktop's listener for other machines looks for a shared session.
const LISTENER_POLL: Duration = Duration::from_secs(1);

/// Where the links of this backend's sessions point.
pub enum Hosting {
    /// Nobody else can reach this backend, so its sessions have no join link.
    None,
    /// The desktop backend: a listener opened when a session starts sharing and closed once none is shared.
    Lan(Arc<ShareListener>),
    /// A server other machines already reach at this base.
    Server(LinkBase),
}

impl Hosting {
    /// Makes the backend reachable before a session announces its link; a failure only costs the join link.
    pub(crate) async fn open(&self) {
        if let Self::Lan(listener) = self {
            let failure = listener.ensure_open().await.err().map(|error| {
                tracing::warn!("Sessions cannot be shared with other machines: {error}");
                error.to_string()
            });
            *listener
                .problem
                .lock()
                .unwrap_or_else(PoisonError::into_inner) = failure;
            // The session list says why there is no link.
            listener.state.sessions.listing_changed();
        }
    }

    /// Why sessions have no join link, when the listener other machines reach could not be opened.
    pub(crate) fn link_problem(&self) -> Option<String> {
        match self {
            Self::Lan(listener) => listener
                .problem
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .clone(),
            Self::None | Self::Server(_) => None,
        }
    }

    /// The URL that admits its holder to the session `link` opens, if other machines can reach this backend.
    pub(crate) fn join_url(&self, link: &JoinLink) -> Option<String> {
        let base = match self {
            Self::None => return None,
            Self::Lan(listener) => listener.base()?,
            Self::Server(base) => base.clone(),
        };
        Some(base.link(&format!("join={link}")))
    }
}

struct Open {
    base: LinkBase,
    serving: JoinHandle<()>,
}

/// The listener the desktop opens for other machines when a session starts sharing and closes once none is shared. It
/// serves the same frontend, `/api/info` and the session and playback sockets, and accepts join links only: never
/// the server password, settings or the session list.
pub struct ShareListener {
    address: SocketAddr,
    state: AppState,
    open: Mutex<Option<Open>>,
    /// Held while opening or closing, so the two never overlap.
    changing: AsyncMutex<()>,
    /// Why the last attempt to open failed; cleared by an attempt that succeeds.
    problem: Mutex<Option<String>>,
}

impl ShareListener {
    /// A listener for `address` that is closed until a session is shared. It serves the sessions of `local`.
    pub fn new(address: SocketAddr, local: &AppState) -> Arc<Self> {
        Arc::new(Self {
            address,
            state: local.shared_with(Policy::Shared {
                password: None,
                settings: Arc::clone(&local.settings),
            }),
            open: Mutex::new(None),
            changing: AsyncMutex::new(()),
            problem: Mutex::new(None),
        })
    }

    /// The base of the links while the listener is open.
    pub fn base(&self) -> Option<LinkBase> {
        let open = self.open.lock().unwrap_or_else(PoisonError::into_inner);
        open.as_ref().map(|open| open.base.clone())
    }

    /// Opens the listener if it is closed, and returns the base of its links. It closes again once no session is
    /// shared.
    pub async fn ensure_open(self: &Arc<Self>) -> Result<LinkBase, ShareError> {
        let _changing = self.changing.lock().await;
        if let Some(base) = self.base() {
            return Ok(base);
        }
        refuse_development_build().map_err(ShareError)?;
        let listener = tokio::net::TcpListener::bind(self.address)
            .await
            .map_err(|error| ShareError(format!("cannot listen on {}: {error}", self.address)))?;
        let bound = listener
            .local_addr()
            .map_err(|error| ShareError(error.to_string()))?;
        let base = LinkBase::for_listener(None, bound);
        if !base.is_https() {
            tracing::warn!(
                "Sharing plain HTTP on {bound} while a session is shared: what is shown crosses the network unencrypted. \
                 Use a trusted network."
            );
        }
        let app = lan_app(self.state.clone()).into_make_service_with_connect_info::<SocketAddr>();
        let mut stopping = self.state.stopping.clone();
        let serving = tokio::spawn(async move {
            // Its sockets end when the backend stops (see `session::socket`), so this does not wait them out.
            let served = axum::serve(listener, app).with_graceful_shutdown(async move {
                let _ = stopping.wait_for(|stopping| *stopping).await;
            });
            if let Err(error) = served.await {
                tracing::error!("The sharing listener stopped: {error}");
            }
        });
        *self.open.lock().unwrap_or_else(PoisonError::into_inner) = Some(Open {
            base: base.clone(),
            serving,
        });
        // The session list carries the join links, which exist while this is open.
        self.state.sessions.listing_changed();
        let this = Arc::clone(self);
        tokio::spawn(async move { this.close_when_empty().await });
        Ok(base)
    }

    /// The sessions service has no end notification, and a session also stops being shared when its owner goes away,
    /// so look.
    async fn close_when_empty(&self) {
        let mut stopping = self.state.stopping.clone();
        loop {
            let stop = tokio::select! {
                () = tokio::time::sleep(LISTENER_POLL) => false,
                _ = stopping.wait_for(|stopping| *stopping) => true,
            };
            if stop {
                return;
            }
            let _changing = self.changing.lock().await;
            if self.state.sessions.is_empty() {
                let open = self
                    .open
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner)
                    .take();
                if let Some(open) = open {
                    open.serving.abort();
                    tracing::info!("Closed the sharing listener: no session is shared");
                    self.state.sessions.listing_changed();
                }
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::TestApp;

    #[tokio::test]
    async fn a_listener_that_cannot_open_says_why_there_is_no_link() {
        let app = TestApp::new();
        let taken = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let hosting = Hosting::Lan(ShareListener::new(taken.local_addr().unwrap(), &app.state));
        assert_eq!(hosting.link_problem(), None);
        let listed = app.state.sessions.listing_revisions();

        hosting.open().await;
        assert!(
            listed.has_changed().unwrap(),
            "the list says why there is no link"
        );

        // A development build refuses before it binds, with its own reason.
        let wanted = if cfg!(feature = "embed-assets") {
            "cannot listen on"
        } else {
            "development build"
        };
        let problem = hosting.link_problem().unwrap();
        assert!(problem.contains(wanted), "{problem}");
    }
}
