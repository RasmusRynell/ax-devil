//! Finding Axis cameras on a range of addresses: a TCP probe of the web ports, then the camera's own answer to
//! "who are you" on each open one, with no login. Only a host that answered as an Axis camera is ever sent a login,
//! as an answer to its own challenge.

mod host;
mod targets;

use std::net::Ipv4Addr;
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::{Semaphore, mpsc};
use tokio::task::{JoinHandle, JoinSet};
use tokio::time::timeout;

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

    /// The longest the scan waits for hosts still being identified once every address has been visited.
    const fn drain(self) -> Duration {
        match self {
            Self::Normal => Duration::from_secs(20),
            Self::Startup => Duration::from_secs(80),
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

/// The scan itself broke before it could finish: the cameras found until then are all there will be.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ScanFailed;

/// Scans `targets` in the background (a tokio runtime must be running) and returns the cameras as each is found, in no
/// particular order; the channel closes when the scan is over, after one `Err` if the scan broke instead of finishing.
/// Dropping the receiver stops the scan, including any sign-in not yet sent.
///
/// Once every address has been visited, hosts still being identified get 20 s (80 s for [`ScanLoad::Startup`]); then
/// they are stopped, nothing more is sent, and the channel closes with the cameras found so far.
///
/// A camera is found without a login. With `logins` it is also signed in to, in order, until one is accepted, but
/// only once it has answered as an Axis camera. A camera that turns a login away
/// for now (429, or 403 after a refusal) is asked again after 1 s, then 2 s. A login with no user name is skipped.
pub fn scan(
    targets: Targets,
    logins: Vec<Login>,
    load: ScanLoad,
    ports: ScanPorts,
) -> mpsc::Receiver<Result<Found, ScanFailed>> {
    scan_with(targets, logins, load, ports, load.drain())
}

/// [`scan`] with its own bound on how long the hosts still being identified are waited for.
fn scan_with(
    targets: Targets,
    logins: Vec<Login>,
    load: ScanLoad,
    ports: ScanPorts,
    drain: Duration,
) -> mpsc::Receiver<Result<Found, ScanFailed>> {
    let (tx, rx) = mpsc::channel(QUEUE);
    let report = tx.clone();
    let task = tokio::spawn(async move {
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
            // The drain runs from the end of probing, as Python's does: every probe permit back means no host is
            // still being probed, only identified.
            let Ok(_probed) = probe.acquire_many(load.probes() as u32).await else {
                return;
            };
            let finished = timeout(drain, async {
                while let Some(done) = hosts.join_next().await {
                    // A host that panicked broke the scan: this task ends by the same panic, which is reported.
                    if let Err(error) = done
                        && let Ok(panic) = error.try_into_panic()
                    {
                        std::panic::resume_unwind(panic);
                    }
                }
            });
            if finished.await.is_err() {
                // Stopped here, before the channel closes, so no camera arrives after the deadline.
                hosts.shutdown().await;
            }
        };
        tokio::select! {
            () = tx.closed() => {}
            () = drive => {}
        }
    });
    report_panic(task, report);
    rx
}

/// Sends `Err` to `report` if `task` panics, so a scan that broke is not mistaken for one that found nothing more.
fn report_panic(task: JoinHandle<()>, report: mpsc::Sender<Result<Found, ScanFailed>>) {
    tokio::spawn(async move {
        if task.await.is_err_and(|error| error.is_panic()) {
            tracing::warn!("A scan task panicked");
            // A receiver that is gone has stopped the scan and wants no report.
            let _ = report.send(Err(ScanFailed)).await;
        }
    });
}

/// Signs in to the camera at `addr` on `web` with `login`, and tells what it said: the scan's way to one host, for a
/// login the app already holds. Nothing is scanned. Like a scan, no login is sent until the host answered as an Axis
/// camera; `None` when nothing there did.
pub async fn check(addr: Ipv4Addr, web: Web, login: Login) -> Option<Found> {
    host::identify(addr, &[web], &[login]).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn a_scan_task_that_panics_is_reported_apart_from_one_that_ends() {
        let (tx, mut rx) = mpsc::channel(1);
        report_panic(tokio::spawn(async { panic!("the scan breaks") }), tx);
        assert_eq!(rx.recv().await, Some(Err(ScanFailed)));
        assert!(rx.recv().await.is_none());

        let (tx, mut rx) = mpsc::channel(1);
        report_panic(tokio::spawn(async {}), tx);
        assert!(rx.recv().await.is_none());
    }

    // The drain test needs a second loopback address, which only Linux has without setup.
    #[cfg(target_os = "linux")]
    use {
        std::time::Instant,
        tokio::io::{AsyncReadExt, AsyncWriteExt},
        tokio::net::TcpListener,
    };

    #[cfg(target_os = "linux")]
    const INFO: &str = r#"{"apiVersion":"1.3","data":{"propertyList":{"Brand":"AXIS","ProdShortName":"AXIS Q1656","SerialNumber":"ACCC8E000001","Version":"11.11.1","ProdType":"Box Camera"}}}"#;

    #[cfg(target_os = "linux")]
    #[tokio::test]
    async fn a_host_still_being_identified_after_the_drain_is_stopped_and_the_scan_ends() {
        // 127.0.0.1 answers at once on one port; 127.0.0.2 accepts on the other and never says a word.
        let camera = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let hung = TcpListener::bind("127.0.0.2:0").await.expect("bind");
        let web = |listener: &TcpListener| Web {
            scheme: Scheme::Http,
            port: listener.local_addr().expect("addr").port(),
        };
        let ports = ScanPorts::new([web(&hung), web(&camera)]);
        tokio::spawn(async move {
            loop {
                let (mut stream, _) = camera.accept().await.expect("accept");
                tokio::spawn(async move {
                    let _ = stream.read(&mut [0; 4096]).await;
                    let reply = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{INFO}",
                        INFO.len()
                    );
                    let _ = stream.write_all(reply.as_bytes()).await;
                });
            }
        });
        tokio::spawn(async move {
            let mut held = Vec::new();
            while let Ok((stream, _)) = hung.accept().await {
                held.push(stream);
            }
        });

        let targets = Targets::parse("127.0.0.1,127.0.0.2").expect("targets");
        let started = Instant::now();
        let mut found = scan_with(
            targets,
            Vec::new(),
            ScanLoad::Normal,
            ports,
            Duration::from_millis(300),
        );
        let mut addrs = Vec::new();
        while let Some(camera) = found.recv().await {
            addrs.push(camera.expect("the scan finishes").addr);
        }

        assert_eq!(
            addrs,
            [Ipv4Addr::LOCALHOST],
            "only the camera that answered"
        );
        assert!(
            started.elapsed() < Duration::from_secs(1),
            "the scan ended at the drain, not at the hung host's request timeout"
        );
    }

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
