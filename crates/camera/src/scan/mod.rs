//! Finding Axis cameras on a range of addresses: a TCP probe of the web ports, then the camera's own answer to
//! "who are you" on each open one, with no login. Only a host that answered as an Axis camera is ever sent a login,
//! as a Digest answer to its own challenge.

mod host;
mod targets;

use std::net::Ipv4Addr;
use std::sync::Arc;

use tokio::sync::{Semaphore, mpsc};
use tokio::task::JoinSet;

use crate::target::{Login, Scheme, Web};
use crate::vapix::Identity;
pub use targets::{MAX_TARGETS, Targets};

/// How many found cameras may wait for the app.
const QUEUE: usize = 64;

/// How hard a scan works the network.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ScanLoad {
    /// A scan the user asked for.
    Normal,
    /// A scan the app starts by itself: a quarter of the connections.
    Startup,
}

impl ScanLoad {
    /// How many hosts are probed at once.
    const fn probes(self) -> usize {
        match self {
            Self::Normal => 128,
            Self::Startup => 32,
        }
    }

    /// How many hosts are identified and signed in to at once.
    const fn identifies(self) -> usize {
        match self {
            Self::Normal => 32,
            Self::Startup => 8,
        }
    }
}

/// The web ports a scan tries on each host, in order; the first that answers as a camera is the one reported.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScanPorts(Vec<Web>);

impl ScanPorts {
    /// These ports, tried in this order.
    pub fn new(ports: impl IntoIterator<Item = Web>) -> Self {
        Self(ports.into_iter().collect())
    }
}

impl ScanPorts {
    /// The standard ports, `preferred` first: the other is asked only when the preferred one does not answer as a
    /// camera.
    pub fn preferring(preferred: Scheme) -> Self {
        let other = match preferred {
            Scheme::Http => Scheme::Https,
            Scheme::Https => Scheme::Http,
        };
        Self::new([preferred, other].map(|scheme| Web {
            scheme,
            port: scheme.default_port(),
        }))
    }
}

/// Whether the scan signed in to a camera. The password is in none of these.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SignIn {
    /// No login was given, or none had a user name.
    NotTried,
    /// The login at `login_index` in the scan's list was accepted.
    SignedIn {
        /// Which of the scan's logins.
        login_index: usize,
        /// The architecture, such as `aarch64`; empty if the camera did not say.
        architecture: String,
        /// The system on chip; empty if the camera did not say.
        soc: String,
    },
    /// Every login was turned away.
    Refused,
    /// Signing in did not work (an error, or a camera still turning logins away after the retries).
    Failed,
}

/// An Axis camera that answered the scan.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Found {
    /// Its address.
    pub addr: Ipv4Addr,
    /// The web port that answered.
    pub web: Web,
    /// What it said about itself without a login.
    pub identity: Identity,
    /// How long it has been up, if it said.
    pub uptime: Option<std::time::Duration>,
    /// The outcome of signing in.
    pub sign_in: SignIn,
}

/// Scans `targets` in the background (a tokio runtime must be running) and returns the cameras as each is found, in no
/// particular order; the channel closes when the scan is over. Dropping the receiver stops the scan, including any
/// sign-in not yet sent.
///
/// A camera is found without a login. With `logins` it is also signed in to, in order, until one is accepted, but
/// only once it has answered as an Axis camera, and only with Digest, never Basic. A camera that turns a login away
/// for now (429, or 403 after a refusal) is asked again after 1 s, then 2 s. A login with no user name is skipped.
pub fn scan(
    targets: Targets,
    logins: Vec<Login>,
    load: ScanLoad,
    ports: ScanPorts,
) -> mpsc::Receiver<Found> {
    let (tx, rx) = mpsc::channel(QUEUE);
    tokio::spawn(async move {
        let shared = Arc::new(host::Shared {
            logins,
            ports,
            identify: Semaphore::new(load.identifies()),
        });
        let probe = Arc::new(Semaphore::new(load.probes()));
        // Hosts run in a set that aborts them when this task is dropped, so a closed receiver ends them all.
        let mut hosts = JoinSet::new();
        let drive = async {
            for &addr in targets.addresses() {
                let Ok(permit) = Arc::clone(&probe).acquire_owned().await else {
                    return;
                };
                hosts.spawn(host::visit(addr, Arc::clone(&shared), permit, tx.clone()));
            }
            while hosts.join_next().await.is_some() {}
        };
        tokio::select! {
            () = tx.closed() => {}
            () = drive => {}
        }
    });
    rx
}

/// Signs in to the camera at `addr` on `web` with `login`, and tells what it said: the scan's way to one host, for a
/// login the app already holds. Nothing is scanned. Like a scan, no login is sent until the host answered as an Axis
/// camera, and only as Digest; `None` when nothing there did.
pub async fn check(addr: Ipv4Addr, web: Web, login: Login) -> Option<Found> {
    host::identify(addr, &[web], &[login]).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_preferred_scheme_is_asked_first_on_its_standard_port() {
        for (preferred, other) in [(Scheme::Https, Scheme::Http), (Scheme::Http, Scheme::Https)] {
            let ports = ScanPorts::preferring(preferred).0;
            let order: Vec<_> = ports.iter().map(|web| (web.scheme, web.port)).collect();
            assert_eq!(
                order,
                [
                    (preferred, preferred.default_port()),
                    (other, other.default_port())
                ]
            );
        }
    }
}
