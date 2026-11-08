//! One address of a scan: which web port is open, whether it is an Axis camera, and whether a login signs in.

use std::net::Ipv4Addr;
use std::time::Duration;

use tokio::net::TcpStream;
use tokio::sync::{OwnedSemaphorePermit, Semaphore, mpsc};
use tokio::time::{sleep, timeout};
use tracing::debug;

use super::{Found, ScanFailed, ScanPorts, SignIn};
use crate::device::Device;
use crate::error::{CameraError, ErrorKind};
use crate::target::{Camera, Host, Login, Web};

const CONNECT_TIMEOUT: Duration = Duration::from_millis(500);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(2);
/// The waits before a login a camera turned away for now is sent again.
const RETRY_WAITS: [Duration; 2] = [Duration::from_secs(1), Duration::from_secs(2)];

/// What every host of a scan shares.
pub(super) struct Shared {
    pub(super) logins: Vec<Login>,
    pub(super) ports: ScanPorts,
    pub(super) identify: Semaphore,
}

/// Visits `addr` and sends the camera found there, if any. `probe` is held while the ports are probed.
pub(super) async fn visit(
    addr: Ipv4Addr,
    shared: std::sync::Arc<Shared>,
    probe: OwnedSemaphorePermit,
    found: mpsc::Sender<Result<Found, ScanFailed>>,
) {
    let open = open_ports(addr, &shared.ports).await;
    drop(probe);
    if open.is_empty() {
        return;
    }
    let Ok(_identify) = shared.identify.acquire().await else {
        return;
    };
    if let Some(camera) = identify(addr, &open, &shared.logins).await {
        // A closed receiver ends the whole scan; there is nobody to tell.
        let _ = found.send(Ok(camera)).await;
    }
}

/// The ports among `ports` that accept a connection in time, in `ports` order.
async fn open_ports(addr: Ipv4Addr, ports: &ScanPorts) -> Vec<Web> {
    let probes = ports.0.iter().map(|web| async move {
        let connect = TcpStream::connect((addr, web.port));
        timeout(CONNECT_TIMEOUT, connect)
            .await
            .is_ok_and(|r| r.is_ok())
    });
    let open = futures_util::future::join_all(probes).await;
    ports
        .0
        .iter()
        .zip(open)
        .filter_map(|(web, open)| open.then_some(*web))
        .collect()
}

/// The Axis camera at `addr` on the first of `open` that answers as one, signed in to if there are logins.
pub(super) async fn identify(addr: Ipv4Addr, open: &[Web], logins: &[Login]) -> Option<Found> {
    for &web in open {
        let mut camera = Camera::new(Host::parse(&addr.to_string()).ok()?);
        camera.web = web;
        camera.timeout = REQUEST_TIMEOUT;
        let device = match Device::new(&camera) {
            Ok(device) => device,
            Err(error) => {
                debug!("scan: no client for {addr}: {error}");
                return None;
            }
        };
        // No login is sent here: this host may be anything.
        let identity = match device.identify().await {
            Ok(identity) => identity,
            Err(error) => {
                debug!("scan: {addr} port {} is not a camera: {error}", web.port);
                continue;
            }
        };
        let uptime = device.uptime().await.ok();
        let sign_in = sign_in(addr, &camera, logins).await;
        return Some(Found {
            addr,
            web,
            identity,
            uptime,
            sign_in,
        });
    }
    None
}

/// Tries `logins` in order on the camera, Digest only. A camera turning logins away for now is asked again after a
/// wait; any other failure ends the attempt, so a broken camera is not asked again and again.
async fn sign_in(addr: Ipv4Addr, camera: &Camera, logins: &[Login]) -> SignIn {
    let mut refusals = 0;
    let mut tried = false;
    for (login_index, login) in logins.iter().enumerate() {
        if login.user.is_empty() {
            continue;
        }
        tried = true;
        let mut waits = RETRY_WAITS.into_iter();
        loop {
            match try_login(camera, login).await {
                Ok(identity) => {
                    return SignIn::SignedIn {
                        login_index,
                        architecture: identity.architecture,
                        soc: identity.soc,
                    };
                }
                Err(error) if error.kind == ErrorKind::LoginRejected => {
                    refusals += 1;
                    break;
                }
                Err(error) => {
                    let busy = error.kind == ErrorKind::Throttled
                        || (error.kind == ErrorKind::Forbidden && refusals > 0);
                    match waits.next() {
                        Some(wait) if busy => sleep(wait).await,
                        _ => {
                            debug!("scan: could not sign in to {addr}: {error}");
                            return SignIn::Failed;
                        }
                    }
                }
            }
        }
    }
    if tried {
        SignIn::Refused
    } else {
        SignIn::NotTried
    }
}

async fn try_login(camera: &Camera, login: &Login) -> Result<crate::vapix::Identity, CameraError> {
    let mut camera = camera.clone();
    camera.login = Some(login.clone());
    Device::new(&camera)?.digest_only().sign_in().await
}
