//! Sources shared by every viewer of the same file.

use crate::source::{DECODE_WORKERS, Source};
use crate::{CachePool, DecoderFactory, Session, SourceSpec, lock};
use foundation::{ErrorCode, HostPath, UserError};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex, Weak};
use std::time::Duration;

/// Counts running decode threads, so shutdown can wait for them once, with one deadline.
#[derive(Default)]
struct Retired {
    running: Mutex<usize>,
    changed: Condvar,
}

struct Running(Arc<Retired>);

impl Retired {
    fn enter(self: &Arc<Self>) -> Running {
        *lock(&self.running) += 1;
        Running(Arc::clone(self))
    }
}

impl Drop for Running {
    fn drop(&mut self) {
        *lock(&self.0.running) -= 1;
        self.0.changed.notify_all();
    }
}

struct Shared {
    pool: Arc<CachePool>,
    sources: Mutex<HashMap<PathBuf, Weak<Source>>>,
    retired: Arc<Retired>,
}

impl Shared {
    fn live(&self) -> Vec<Arc<Source>> {
        lock(&self.sources)
            .values()
            .filter_map(Weak::upgrade)
            .collect()
    }

    /// Every source re-reads its cache share: another source joined or left, or the allowance changed.
    fn poke_all(&self) {
        for source in self.live() {
            source.poke();
        }
    }
}

/// Open sources keyed by canonical path, which the registry works out itself, once per call. Viewers of one file
/// share its decoders, its cache share and its frames. Errors the registry and its sources raise carry that path as
/// their location.
pub struct Registry {
    shared: Arc<Shared>,
}

fn canonical(path: &Path) -> Result<PathBuf, UserError> {
    std::fs::canonicalize(path).map_err(|error| {
        UserError::new(
            ErrorCode::FileUnreadable,
            format!("Cannot open the video: {error}"),
        )
        .at(HostPath::new(path))
    })
}

impl Registry {
    /// A registry whose sources share `pool`.
    pub fn new(pool: Arc<CachePool>) -> Self {
        Self {
            shared: Arc::new(Shared {
                pool,
                sources: Mutex::new(HashMap::new()),
                retired: Arc::new(Retired::default()),
            }),
        }
    }

    /// A new viewer of `path` when it is already open.
    pub fn attach(&self, path: &Path) -> Option<Session> {
        let key = canonical(path).ok()?;
        let source = lock(&self.shared.sources).get(&key)?.upgrade()?;
        Session::join(&source)
    }

    /// A viewer of `path`. Opens the source, starting its decode workers, unless it is already open, in which
    /// case `spec` and `factory` are ignored and the viewer joins the open one: of two openers racing for the same
    /// file, both end up on one source. The session's [`Session::spec`] says which spec the source has.
    pub fn open(
        &self,
        path: &Path,
        spec: SourceSpec,
        factory: DecoderFactory,
    ) -> Result<Session, UserError> {
        let key = canonical(path)?;
        let (source, session) = {
            let mut sources = lock(&self.shared.sources);
            if let Some(session) = sources
                .get(&key)
                .and_then(Weak::upgrade)
                .and_then(|source| Session::join(&source))
            {
                return Ok(session);
            }
            let location = HostPath::new(&key);
            let source = Source::new(spec, factory, self.shared.pool.add_source(), location);
            let me = Arc::downgrade(&source);
            let registry = Arc::downgrade(&self.shared);
            let own_key = key.clone();
            source.set_on_close(Box::new(move || {
                let Some(shared) = registry.upgrade() else {
                    return;
                };
                {
                    let mut sources = lock(&shared.sources);
                    if sources.get(&own_key).is_some_and(|weak| weak.ptr_eq(&me)) {
                        sources.remove(&own_key);
                    }
                }
                shared.poke_all();
            }));
            sources.insert(key, Arc::downgrade(&source));
            let session = Session::join(&source);
            (source, session)
        };
        // Outside the registry lock: a failed start closes the source, which takes that lock.
        self.spawn_workers(&source)?;
        self.shared.poke_all();
        session.ok_or_else(|| {
            UserError::new(ErrorCode::VideoClosed, "The video was closed while opening")
                .at(HostPath::new(path))
        })
    }

    fn spawn_workers(&self, source: &Arc<Source>) -> Result<(), UserError> {
        for index in 0..DECODE_WORKERS {
            let running = self.shared.retired.enter();
            let worker_source = Arc::clone(source);
            let spawned = std::thread::Builder::new()
                .name(format!("playback-decode-{index}"))
                .spawn(move || {
                    let _running = running;
                    worker_source.run_worker(index);
                });
            if let Err(error) = spawned {
                source.close();
                return Err(UserError::new(
                    ErrorCode::Internal,
                    format!("Cannot start the video decoder: {error}"),
                )
                .at(source.location.clone()));
            }
        }
        Ok(())
    }

    /// Applies a new cache allowance to every open source at once.
    pub fn set_allowance(&self, bytes: usize) {
        self.shared.pool.set_allowance(bytes);
        self.shared.poke_all();
    }

    /// Decode threads still running, including those of closed sources that are finishing a frame.
    pub fn running_workers(&self) -> usize {
        *lock(&self.shared.retired.running)
    }

    /// Waits for every decode thread to end, up to `timeout`; true when none is left. Blocks: call it from a
    /// blocking thread, once, at shutdown. A decode that never returns costs only its own thread.
    pub fn wait_retired(&self, timeout: Duration) -> bool {
        let retired = &self.shared.retired;
        let guard = lock(&retired.running);
        let (guard, _) = retired
            .changed
            .wait_timeout_while(guard, timeout, |running| *running > 0)
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        *guard == 0
    }
}
