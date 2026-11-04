//! The network scan and the questions the live form asks a camera: what the cameras found on a range of addresses
//! are, which stream sizes a camera offers and which analytics it publishes. The routes that serve them are adapters
//! over these functions.
//!
//! A scan is the only thing that contacts hosts on its own account, and only when the user (or the setting that
//! starts one at launch) asks. Opening a viewer is what contacts a camera for a stream; the sidebar's snapshots and
//! login checks ([`crate::glance`]) go only to hosts a viewer opened for or the user confirmed. Passwords never leave a
//! [`camera::Secret`]: a row has no login, only which of the configured logins was accepted.

use std::{
    net::Ipv4Addr,
    sync::{Arc, Mutex, PoisonError},
    time::Duration,
};

use api_types::live::{
    LiveProtocol, LiveReason, LiveResolution, LiveResolutions, LiveSources as SourcesWire,
    NetworkIdentity, NetworkRow, NetworkSignIn, NetworkSnapshot,
};
use camera::{
    Device, Found, Head, Login, ScanLoad, ScanPorts, Scheme, Secret, SignIn, Targets, scan,
};
use foundation::{ErrorCode, Location, UserError};
use tokio::{
    sync::{mpsc, watch},
    task::JoinHandle,
    time::{MissedTickBehavior, interval},
};
use workspace::ResolvedCamera;

use crate::live_sources::{camera_of, reason};

/// How often a running scan publishes the cameras it found since the last time.
pub const SNAPSHOT_EVERY: Duration = Duration::from_millis(100);

/// What to scan.
pub struct ScanRequest {
    /// The addresses.
    pub targets: Targets,
    /// The logins to try, in order, on a device that answered as an Axis camera.
    pub logins: Vec<Login>,
    /// How hard to work the network.
    pub load: ScanLoad,
    /// The web ports to try on each host.
    pub ports: ScanPorts,
}

/// Starts a scan: the real one, or one a test supplies.
type Scanner = Box<dyn Fn(ScanRequest) -> mpsc::Receiver<Found> + Send + Sync>;

struct Running {
    scan: u64,
    /// The running scan's task; `None` once it is cancelled or before the first scan, when nothing may publish.
    task: Option<JoinHandle<()>>,
    /// The logins this scan tried, which the `login_index` of its rows counts in. Settings may change meanwhile, so
    /// the index is never read against them. They stay on the server.
    logins: Vec<Login>,
}

/// The network scan of the app: one scan at a time, whose snapshots reach any number of readers.
pub struct Network {
    running: Mutex<Running>,
    snapshots: watch::Sender<Arc<NetworkSnapshot>>,
    scanner: Scanner,
}

/// The wire form of what a scan or a login check found.
pub(crate) fn row(found: &Found) -> NetworkRow {
    NetworkRow {
        address: found.addr.to_string(),
        scheme: match found.web.scheme {
            Scheme::Http => LiveProtocol::Http,
            Scheme::Https => LiveProtocol::Https,
        },
        port: found.web.port,
        identity: NetworkIdentity {
            model: found.identity.model.clone(),
            serial: found.identity.serial.clone(),
            firmware: found.identity.firmware.clone(),
            product_type: found.identity.product_type.clone(),
        },
        uptime_s: found.uptime.map(|uptime| uptime.as_secs()),
        sign_in: match &found.sign_in {
            SignIn::NotTried => NetworkSignIn::NotTried,
            SignIn::SignedIn {
                login_index,
                architecture,
                soc,
            } => NetworkSignIn::SignedIn {
                login_index: u32::try_from(*login_index).unwrap_or(u32::MAX),
                architecture: architecture.clone(),
                soc: soc.clone(),
            },
            SignIn::Refused => NetworkSignIn::Refused,
            SignIn::Failed => NetworkSignIn::Failed,
        },
    }
}

impl Network {
    /// A service with no scan run yet. Scans run on the Tokio runtime it is used in.
    pub fn new() -> Arc<Self> {
        Self::with_scanner(|request| {
            scan(request.targets, request.logins, request.load, request.ports)
        })
    }

    /// A service whose scans find what `scanner` sends instead of probing the network.
    pub fn with_scanner(
        scanner: impl Fn(ScanRequest) -> mpsc::Receiver<Found> + Send + Sync + 'static,
    ) -> Arc<Self> {
        Arc::new(Self {
            scanner: Box::new(scanner),
            running: Mutex::new(Running {
                scan: 0,
                task: None,
                logins: Vec::new(),
            }),
            snapshots: watch::channel(Arc::new(NetworkSnapshot {
                scan: 0,
                scanning: false,
                rows: Vec::new(),
            }))
            .0,
        })
    }

    /// The snapshots: the current one at once, then each change, at most every [`SNAPSHOT_EVERY`] while scanning.
    pub fn subscribe(&self) -> watch::Receiver<Arc<NetworkSnapshot>> {
        self.snapshots.subscribe()
    }

    /// The snapshot now.
    pub fn snapshot(&self) -> Arc<NetworkSnapshot> {
        self.snapshots.borrow().clone()
    }

    /// Starts a scan, ending the one that is running: the rows start empty. Returns the scan's number.
    pub fn rescan(self: &Arc<Self>, request: ScanRequest) -> u64 {
        let mut running = self.running.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(task) = running.task.take() {
            task.abort();
        }
        running.scan += 1;
        running.logins.clone_from(&request.logins);
        let number = running.scan;
        self.snapshots.send_replace(Arc::new(NetworkSnapshot {
            scan: number,
            scanning: true,
            rows: Vec::new(),
        }));
        let network = Arc::clone(self);
        running.task = Some(tokio::spawn(async move {
            network.run(number, request).await;
        }));
        number
    }

    /// Stops the running scan, keeping what it found. Does nothing when none is running.
    pub fn cancel(&self) {
        let mut running = self.running.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(task) = running.task.take() {
            task.abort();
        }
        let now = self.snapshot();
        if now.scanning {
            self.snapshots.send_replace(Arc::new(NetworkSnapshot {
                scanning: false,
                ..(*now).clone()
            }));
        }
    }

    /// The login that scan `scan` signed in with as its `index`th: `None` when a newer scan has begun, which has its own
    /// logins, or the scan tried no such login.
    pub fn accepted_login(&self, scan: u64, index: u32) -> Option<Login> {
        let running = self.running.lock().unwrap_or_else(PoisonError::into_inner);
        if running.scan != scan {
            return None;
        }
        running.logins.get(usize::try_from(index).ok()?).cloned()
    }

    /// The camera the current scan found at `address` and the login that signed in to it: `None` when there is no such
    /// row, the scan did not sign in to it, or a newer scan has begun. The login stays on the server.
    pub fn signed_in(&self, address: &str) -> Option<(NetworkRow, Login)> {
        let scan = self.snapshot();
        let row = scan.rows.iter().find(|row| row.address == address)?;
        let NetworkSignIn::SignedIn { login_index, .. } = &row.sign_in else {
            return None;
        };
        let login = self.accepted_login(scan.scan, *login_index)?;
        Some((row.clone(), login))
    }

    /// Publishes scan `number`'s state unless a newer scan began or it was cancelled.
    fn publish(&self, number: u64, rows: &[(Ipv4Addr, NetworkRow)], scanning: bool) {
        let running = self.running.lock().unwrap_or_else(PoisonError::into_inner);
        if running.scan != number || running.task.is_none() {
            return;
        }
        let mut rows = rows.to_vec();
        rows.sort_by_key(|(addr, _)| *addr);
        self.snapshots.send_replace(Arc::new(NetworkSnapshot {
            scan: number,
            scanning,
            rows: rows.into_iter().map(|(_, row)| row).collect(),
        }));
    }

    async fn run(&self, number: u64, request: ScanRequest) {
        let mut found = (self.scanner)(request);
        let mut rows: Vec<(Ipv4Addr, NetworkRow)> = Vec::new();
        let mut changed = false;
        let mut tick = interval(SNAPSHOT_EVERY);
        tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                next = found.recv() => match next {
                    Some(camera) => {
                        rows.push((camera.addr, row(&camera)));
                        changed = true;
                    }
                    None => break,
                },
                _ = tick.tick() => {
                    if changed {
                        self.publish(number, &rows, true);
                        changed = false;
                    }
                }
            }
        }
        self.publish(number, &rows, false);
    }
}

/// `$NAME` as the variable's value, anything else as written.
fn expand(text: &str) -> String {
    match text.strip_prefix('$') {
        Some(name) if !name.is_empty() => std::env::var(name).unwrap_or_default(),
        _ => text.to_owned(),
    }
}

/// The logins of `scan.logins`, in order, each field expanded when it is a `$NAME` reference. A scan's
/// `login_index` counts in the list it was started with.
fn configured_logins(state: &settings::State) -> Vec<Login> {
    state
        .stored("scan.logins")
        .and_then(|value| value.as_array())
        .into_iter()
        .flatten()
        .map(|record| {
            let field = |name| {
                record
                    .get(name)
                    .and_then(|value| value.as_str())
                    .map(expand)
                    .unwrap_or_default()
            };
            Login {
                user: field("user"),
                password: Secret::new(field("password")),
            }
        })
        .collect()
}

/// The scan the settings describe, over `range` when given and else `scan.range`; blank is the local network. It asks
/// each host over `camera.protocol` first. Its logins are `scan.logins` unless `scan.sign_in` is off, each field
/// expanded when it is a `$NAME` reference. A range that cannot be scanned is refused with the field named.
pub fn request_from_settings(
    state: &settings::State,
    range: Option<&str>,
    load: ScanLoad,
) -> Result<ScanRequest, UserError> {
    let (text, field) = match range {
        Some(text) => (text, "range"),
        None => (
            state
                .stored("scan.range")
                .and_then(|value| value.as_str())
                .unwrap_or_default(),
            "scan.range",
        ),
    };
    let targets = Targets::parse(text).map_err(|error| {
        UserError::new(ErrorCode::WorkspaceInvalid, error.detail().to_owned())
            .at(Location::field(field))
    })?;
    let signs_in = state
        .stored("scan.sign_in")
        .and_then(|value| value.as_bool())
        .unwrap_or(true);
    let logins = if signs_in {
        configured_logins(state)
    } else {
        Vec::new()
    };
    // `camera.protocol` is the web protocol a scan asks first.
    let preferred = match state
        .stored("camera.protocol")
        .and_then(|value| value.as_str())
    {
        Some("http") => Scheme::Http,
        _ => Scheme::Https,
    };
    Ok(ScanRequest {
        targets,
        logins,
        load,
        ports: ScanPorts::preferring(preferred),
    })
}

/// Starts the scan the app makes by itself at launch when `scan.on_startup` is on: at a quarter of the load, finding
/// hosts and never signing in, since nothing is sent to a host the user has not started a scan for.
pub fn scan_on_startup(network: &Arc<Network>, state: &settings::State) {
    let on = state
        .stored("scan.on_startup")
        .and_then(|value| value.as_bool())
        .unwrap_or(true);
    if !on {
        return;
    }
    match request_from_settings(state, None, ScanLoad::Startup) {
        Ok(request) => {
            network.rescan(ScanRequest {
                logins: Vec::new(),
                ..request
            });
        }
        Err(error) => tracing::warn!("The scan at launch cannot start: {}", error.reason),
    }
}

fn device(camera: &ResolvedCamera, timeout: Duration) -> Result<Device, LiveReason> {
    camera_of(camera, timeout)
        .and_then(|camera| Device::new(&camera))
        .map_err(|error| {
            tracing::warn!("A camera cannot be addressed: {error}");
            reason(error.kind)
        })
}

fn failed(what: &str, error: &camera::CameraError) -> LiveReason {
    tracing::warn!("Asking a camera for {what} failed: {error}");
    reason(error.kind)
}

/// The stream sizes `camera`'s head offers. Contacts the camera, with its login.
pub async fn resolutions(
    camera: &ResolvedCamera,
    timeout: Duration,
) -> Result<LiveResolutions, LiveReason> {
    let head = Head::new(camera.head).ok_or(LiveReason::InvalidTarget)?;
    let sizes = device(camera, timeout)?
        .resolutions(head)
        .await
        .map_err(|error| failed("its resolutions", &error))?;
    Ok(LiveResolutions {
        resolutions: sizes
            .into_iter()
            .map(|size| LiveResolution {
                width: size.width,
                height: size.height,
            })
            .collect(),
    })
}

/// The analytics data sources `camera` can publish over MQTT. Contacts the camera, with its login.
pub async fn analytics_sources(
    camera: &ResolvedCamera,
    timeout: Duration,
) -> Result<SourcesWire, LiveReason> {
    let sources = device(camera, timeout)?
        .analytics_sources()
        .await
        .map_err(|error| failed("its analytics sources", &error))?;
    Ok(SourcesWire {
        sources: sources.into_iter().map(|source| source.0).collect(),
    })
}

/// The DataHub topics `camera` offers. Contacts the camera, with its login.
pub async fn datahub_topics(
    camera: &ResolvedCamera,
    timeout: Duration,
) -> Result<Vec<String>, LiveReason> {
    device(camera, timeout)?
        .datahub_topics()
        .await
        .map_err(|error| failed("its DataHub topics", &error))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn the_scan_at_launch_sends_no_login_but_a_pressed_scan_does() {
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        let state = settings.state();
        let (asked, mut requests) = mpsc::unbounded_channel();
        let network = Network::with_scanner(move |request| {
            asked.send(request.logins.len()).unwrap();
            mpsc::channel(1).1
        });

        scan_on_startup(&network, &state);
        assert_eq!(requests.recv().await, Some(0));
        let pressed = request_from_settings(&state, None, ScanLoad::Normal).unwrap();
        network.rescan(pressed);
        assert_eq!(requests.recv().await, Some(1));
    }

    fn found_signed_in(index: usize) -> Found {
        Found {
            addr: Ipv4Addr::new(10, 9, 9, 2),
            web: camera::Web {
                scheme: Scheme::Http,
                port: 80,
            },
            identity: camera::Identity {
                model: String::new(),
                serial: String::new(),
                firmware: String::new(),
                product_type: String::new(),
                architecture: String::new(),
                soc: String::new(),
            },
            uptime: None,
            sign_in: SignIn::SignedIn {
                login_index: index,
                architecture: String::new(),
                soc: String::new(),
            },
        }
    }

    fn request(users: &[&str]) -> ScanRequest {
        ScanRequest {
            targets: Targets::parse("10.9.9.0/30").unwrap(),
            logins: users
                .iter()
                .map(|user| Login {
                    user: (*user).to_owned(),
                    password: Secret::new("pw".to_owned()),
                })
                .collect(),
            load: ScanLoad::Normal,
            ports: ScanPorts::preferring(Scheme::Http),
        }
    }

    #[tokio::test]
    async fn a_login_index_means_the_login_its_scan_tried_and_nothing_once_another_scan_began() {
        let network = Network::with_scanner(|_| mpsc::channel(1).1);
        let first = network.rescan(request(&["a", "b"]));
        let user = |login: Option<Login>| login.map(|login| login.user);
        assert_eq!(user(network.accepted_login(first, 1)), Some("b".to_owned()));
        assert_eq!(user(network.accepted_login(first, 2)), None);

        let second = network.rescan(request(&["c"]));
        assert_eq!(user(network.accepted_login(first, 0)), None);
        assert_eq!(
            user(network.accepted_login(second, 0)),
            Some("c".to_owned())
        );
    }

    #[tokio::test]
    async fn a_cancel_that_wins_the_race_with_a_publish_leaves_the_scan_not_scanning() {
        let network = Network::with_scanner(|_| mpsc::channel(1).1);
        let number = network.rescan(request(&[]));
        network.cancel();
        // The scan's timer was waiting for the lock cancel held, and publishes once it has it.
        network.publish(
            number,
            &[(Ipv4Addr::new(10, 9, 9, 2), row(&found_signed_in(0)))],
            true,
        );
        assert!(!network.snapshot().scanning);
        network.publish(number, &[], false);
        assert!(!network.snapshot().scanning);
    }
}
