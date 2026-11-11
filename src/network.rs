//! The network scan: what the cameras found on a range of addresses are. The routes that serve it are adapters over
//! these functions.
//!
//! A scan is the only thing that contacts hosts on its own account, and only when the user (or the setting that
//! starts one at launch) asks. Opening a viewer is what contacts a camera for a stream; the sidebar's snapshots and
//! login checks ([`crate::glance`]) go only to hosts a viewer opened for. Passwords never leave a
//! [`camera::Secret`]: a row has no login, only which of the configured logins was accepted.

use std::{
    net::Ipv4Addr,
    panic::AssertUnwindSafe,
    sync::{Arc, Mutex, PoisonError},
    time::Duration,
};

use api_types::live::{NetworkIdentity, NetworkRow, NetworkSignIn, NetworkSnapshot};
use camera::{
    Found, Login, ScanFailed, ScanLoad, ScanPorts, Scheme, Secret, SignIn, Targets, scan,
};
use foundation::{ErrorCode, Location, UserError};
use futures_util::FutureExt;
use tokio::{
    sync::{mpsc, watch},
    task::JoinHandle,
    time::{MissedTickBehavior, interval},
};
use workspace::Protocol;

/// How often a running scan publishes the cameras it found since the last time.
pub const SNAPSHOT_EVERY: Duration = Duration::from_millis(100);

/// What to scan.
pub struct ScanRequest {
    /// The addresses.
    pub targets: Targets,
    /// The logins to try, in order, on a device that answered as an Axis camera, as written: a `$NAME` reference is
    /// expanded when the scan starts.
    pub logins: Vec<Login>,
    /// How hard to work the network.
    pub load: ScanLoad,
    /// The web ports to try on each host.
    pub ports: ScanPorts,
}

/// Starts a scan: the real one, or one a test supplies.
type Scanner = Box<dyn Fn(ScanRequest) -> mpsc::Receiver<Result<Found, ScanFailed>> + Send + Sync>;

/// A login a scan tried: the text the user wrote, and what it was when the scan started.
struct ScanLogin {
    /// Kept in a Live Stream Item made from a found camera, so `$NAME` stays a reference there.
    written: Login,
    /// What the scan, and the sidebar's snapshot of a found camera, send.
    used: Login,
}

impl ScanLogin {
    fn new(written: &Login) -> Self {
        let lookup = |name: &str| std::env::var(name).ok();
        Self {
            used: Login {
                user: expand(&written.user, lookup),
                password: Secret::new(expand(written.password.expose(), lookup)),
            },
            written: written.clone(),
        }
    }
}

struct Running {
    scan: u64,
    /// The running scan's task; `None` once it is cancelled or before the first scan, when nothing may publish.
    task: Option<JoinHandle<()>>,
    /// The logins this scan tried, which the `login_index` of its rows counts in. Settings may change meanwhile, so
    /// the index is never read against them. They stay on the server.
    logins: Vec<ScanLogin>,
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
            Scheme::Http => Protocol::Http,
            Scheme::Https => Protocol::Https,
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
        scanner: impl Fn(ScanRequest) -> mpsc::Receiver<Result<Found, ScanFailed>>
        + Send
        + Sync
        + 'static,
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
                failure: None,
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
    pub fn rescan(self: &Arc<Self>, mut request: ScanRequest) -> u64 {
        let mut running = self.running.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(task) = running.task.take() {
            task.abort();
        }
        running.scan += 1;
        running.logins = request.logins.iter().map(ScanLogin::new).collect();
        request.logins = running
            .logins
            .iter()
            .map(|login| login.used.clone())
            .collect();
        let number = running.scan;
        self.snapshots.send_replace(Arc::new(NetworkSnapshot {
            scan: number,
            scanning: true,
            rows: Vec::new(),
            failure: None,
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

    /// The login that scan `scan` signed in with as its `index`th, `$NAME` references expanded as the scan sent it:
    /// `None` when a newer scan has begun, which has its own logins, or the scan tried no such login.
    pub fn accepted_login(&self, scan: u64, index: u32) -> Option<Login> {
        self.scan_login(scan, index, |login| &login.used)
    }

    /// The camera the current scan found at `address` and the login that signed in to it, as written (a `$NAME`
    /// reference stays one): `None` when there is no such row, the scan did not sign in to it, or a newer scan has
    /// begun. The login stays on the server.
    pub fn signed_in(&self, address: &str) -> Option<(NetworkRow, Login)> {
        let scan = self.snapshot();
        let row = scan.rows.iter().find(|row| row.address == address)?;
        let NetworkSignIn::SignedIn { login_index, .. } = &row.sign_in else {
            return None;
        };
        let login = self.scan_login(scan.scan, *login_index, |login| &login.written)?;
        Some((row.clone(), login))
    }

    fn scan_login(
        &self,
        scan: u64,
        index: u32,
        form: impl Fn(&ScanLogin) -> &Login,
    ) -> Option<Login> {
        let running = self.running.lock().unwrap_or_else(PoisonError::into_inner);
        if running.scan != scan {
            return None;
        }
        running
            .logins
            .get(usize::try_from(index).ok()?)
            .map(|login| form(login).clone())
    }

    /// Publishes scan `number`'s state unless a newer scan began or it was cancelled, which keeps a scan that no
    /// longer owns the snapshot from reporting its end.
    fn publish(
        &self,
        number: u64,
        rows: &[(Ipv4Addr, NetworkRow)],
        scanning: bool,
        failure: Option<String>,
    ) {
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
            failure,
        }));
    }

    /// Runs scan `number` to its end. A scan that breaks, in the scanner or while it is collected, ends as a failure,
    /// with the cameras found until then.
    async fn run(&self, number: u64, request: ScanRequest) {
        let mut rows = Vec::new();
        let ended = AssertUnwindSafe(self.collect(number, request, &mut rows))
            .catch_unwind()
            .await;
        let failure = (!matches!(ended, Ok(true))).then(|| {
            tracing::warn!("Scan {number} stopped unexpectedly");
            "The scan stopped unexpectedly.".to_owned()
        });
        self.publish(number, &rows, false, failure);
    }

    /// Gathers what the scanner finds into `rows`, publishing them every [`SNAPSHOT_EVERY`] as they grow. `false` when
    /// the scanner reported that it broke, `true` when it ran out of cameras to find.
    async fn collect(
        &self,
        number: u64,
        request: ScanRequest,
        rows: &mut Vec<(Ipv4Addr, NetworkRow)>,
    ) -> bool {
        let mut found = (self.scanner)(request);
        let mut changed = false;
        let mut tick = interval(SNAPSHOT_EVERY);
        tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                next = found.recv() => match next {
                    Some(Ok(camera)) => {
                        rows.push((camera.addr, row(&camera)));
                        changed = true;
                    }
                    Some(Err(ScanFailed)) => return false,
                    None => break,
                },
                _ = tick.tick() => {
                    if changed {
                        self.publish(number, rows, true, None);
                        changed = false;
                    }
                }
            }
        }
        true
    }
}

/// A whole-value `$NAME` reference as what `lookup` gives for it (empty when unset), anything else as written.
fn expand(text: &str, lookup: impl Fn(&str) -> Option<String>) -> String {
    match settings::reference(text) {
        Some(name) => lookup(name).unwrap_or_default(),
        None => text.to_owned(),
    }
}

/// The logins of `scan.logins`, in order, as written. A scan's `login_index` counts in the list it was started with.
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
                    .map(str::to_owned)
                    .unwrap_or_default()
            };
            Login {
                user: field("user"),
                password: Secret::new(field("password")),
            }
        })
        .collect()
}

/// The scan the settings describe, over `range` when given and else `scan.range`, expanded when it is a `$NAME`
/// reference; blank is the local network. It asks each host over `camera.protocol` first. Its logins are `scan.logins`
/// unless sign-in is off, which `sign_in` says for this scan and else `scan.sign_in` does; each field is as written,
/// a `$NAME` reference expanded when the scan starts. A range that cannot be scanned is refused with the field named.
/// `references` is whether the caller may read the environment (Full access): without it a `$NAME` range is not
/// expanded, so the refusal cannot carry what the variable holds.
pub fn request_from_settings(
    state: &settings::State,
    range: Option<&str>,
    sign_in: Option<bool>,
    load: ScanLoad,
    references: bool,
) -> Result<ScanRequest, UserError> {
    // A `$NAME` reference in the setting is expanded here, where the scan starts.
    let range_setting = if references {
        state.resolved("scan.range")
    } else {
        state.written("scan.range")
    }
    .and_then(|value| value.as_str().map(str::to_owned));
    let (text, field) = match range {
        Some(text) => (text, "range"),
        None => (range_setting.as_deref().unwrap_or_default(), "scan.range"),
    };
    let targets = Targets::parse(text).map_err(|error| {
        UserError::new(ErrorCode::WorkspaceInvalid, error.detail().to_owned())
            .at(Location::field(field))
    })?;
    let signs_in = sign_in.unwrap_or_else(|| {
        state
            .stored("scan.sign_in")
            .and_then(|value| value.as_bool())
            .unwrap_or(true)
    });
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
    match request_from_settings(state, None, None, ScanLoad::Startup, true) {
        Ok(request) => {
            network.rescan(ScanRequest {
                logins: Vec::new(),
                ..request
            });
        }
        Err(error) => tracing::warn!("The scan at launch cannot start: {}", error.reason),
    }
}

#[cfg(test)]
mod tests;
