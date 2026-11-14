//! Opening tracks and sharing them between viewers.

use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::{Arc, Condvar, Mutex, MutexGuard, OnceLock, PoisonError, Weak},
    time::Duration,
};

use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, Location, UserError, hex};
use handler_api::{Decoder, LaneSource, OverlayRef, Values};
use sha2::{Digest, Sha256};

use crate::{
    ingest::Tuning,
    io::user_error,
    load::{Loaded, Request, load, not_built},
    track::Track,
};

/// The folder under the cache folder that holds the decoded artifacts.
pub const SCENE_FOLDER: &str = "scenes";
/// The extension of a decoded artifact.
pub const SCENE_EXTENSION: &str = "axscene";
/// How many bytes of decoded Scenes each track keeps in memory by default.
pub const SCENE_CACHE_BYTES: usize = 32 << 20;

/// How often a waiting open looks at its cancel token.
const WAIT_SLICE: Duration = Duration::from_millis(20);

/// Who is asking for a track, which decides whether it may start a decode that runs code outside the app.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Admission {
    /// May decode: a stored artifact is used, else the decoder runs.
    Compute,
    /// Uses what is built: a stored artifact is used, but a decoder with code outside the app ([`Decoder::code_hash`])
    /// is never run; the open fails as not built yet. Built-in decoders run as for [`Compute`](Self::Compute).
    Reuse,
}

/// What identifies an opened track: the same key shares one decode.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct TrackKey {
    /// The overlay file, canonical.
    pub path: PathBuf,
    /// The decoder handler id.
    pub handler: String,
    /// The decoder's declared options as canonical JSON ([`Values::canonical`]).
    pub options: String,
    /// The hash of the code outside the app that makes the Scenes ([`Decoder::code_hash`]). It is not in the cache
    /// file name, so an edited plugin finds its old artifact and rebuilds it; but a track still held under the old
    /// hash is not shared with an open under the new one.
    pub code: Option<[u8; 32]>,
}

impl TrackKey {
    /// The artifact file name stem: SHA-256 of the key, hex.
    fn digest(&self) -> String {
        let mut hash = Sha256::new();
        for part in [
            self.path.as_os_str().as_encoded_bytes(),
            self.handler.as_bytes(),
            self.options.as_bytes(),
        ] {
            hash.update(part);
            hash.update([0]);
        }
        hex(&hash.finalize())
    }
}

type DecoderLookup = dyn Fn(&str) -> Option<Arc<dyn Decoder>> + Send + Sync;

/// Opens and shares tracks.
pub struct TrackRegistry {
    pub(crate) cache_dir: Option<PathBuf>,
    decoders: Arc<DecoderLookup>,
    scene_cache_bytes: usize,
    tuning: Tuning,
    slots: Mutex<HashMap<TrackKey, Slot>>,
    /// Where lane files and bundles go when there is no cache folder; removed with the registry.
    pub(crate) scratch: OnceLock<tempfile::TempDir>,
}

enum Slot {
    /// Someone is opening it; others wait for the outcome.
    Loading(Arc<Pending>),
    /// Open as long as anyone holds the track.
    Open(Weak<Track>),
}

/// The outcome of an open that others may be waiting for.
struct Pending {
    state: Mutex<Outcome>,
    changed: Condvar,
}

#[derive(Clone)]
enum Outcome {
    Running,
    Done(Result<Arc<Track>, UserError>),
    /// The opener was cancelled or died; a waiter takes over.
    Abandoned,
}

impl Pending {
    fn set(&self, outcome: Outcome) {
        *self.state.lock().unwrap_or_else(PoisonError::into_inner) = outcome;
        self.changed.notify_all();
    }

    /// Waits for the opener to finish, giving up when `cancel` fires.
    fn wait(&self, cancel: &CancelToken) -> Result<Outcome, foundation::Cancelled> {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        loop {
            if !matches!(*state, Outcome::Running) {
                return Ok(state.clone());
            }
            cancel.check()?;
            state = self
                .changed
                .wait_timeout(state, WAIT_SLICE)
                .unwrap_or_else(PoisonError::into_inner)
                .0;
        }
    }
}

impl TrackRegistry {
    /// A registry that finds decoders by id with `lookup`, keeping artifacts under `cache_dir` when it is given.
    /// Without a cache folder artifacts live in unnamed temporary files and are not reused across opens.
    pub fn new(
        lookup: impl Fn(&str) -> Option<Arc<dyn Decoder>> + Send + Sync + 'static,
        cache_dir: Option<&Path>,
    ) -> Self {
        Self {
            cache_dir: cache_dir.map(Path::to_path_buf),
            decoders: Arc::new(lookup),
            scene_cache_bytes: SCENE_CACHE_BYTES,
            tuning: Tuning::default(),
            slots: Mutex::new(HashMap::new()),
            scratch: OnceLock::new(),
        }
    }

    /// Sets how many bytes of decoded Scenes each track opened from now on keeps in memory.
    #[must_use]
    pub fn with_scene_cache(mut self, bytes: usize) -> Self {
        self.scene_cache_bytes = bytes;
        self
    }

    /// Lowers the ingest limits so a test reaches them with little data.
    #[cfg(test)]
    #[must_use]
    pub(crate) fn with_tuning(mut self, tuning: Tuning) -> Self {
        self.tuning = tuning;
        self
    }

    /// Blocking. [`open_as`](Self::open_as) with [`Admission::Compute`].
    pub fn open(
        &self,
        overlay: &OverlayRef,
        cancel: &CancelToken,
    ) -> Result<Arc<Track>, Interrupt<UserError>> {
        self.open_as(overlay, Admission::Compute, cancel)
    }

    /// Blocking. Shares an open track with the same key; else loads a valid artifact or decodes the file and writes
    /// one. A derived lane first has its input lanes opened with the same `admission` and written as a bundle, which
    /// is then the file that is opened.
    ///
    /// The track is returned once it can be read: for a decoder that gave a header, after its first publication
    /// while the decode goes on ([`snapshot`](crate::TrackRead::snapshot) says how far it is); otherwise when the
    /// decode has ended.
    /// Concurrent opens of one key decode once: the others wait, and one of them takes over if the opener is
    /// cancelled before the track can be read; under [`Admission::Reuse`] none takes over, since that would start a
    /// decode of plugin code.
    pub fn open_as(
        &self,
        overlay: &OverlayRef,
        admission: Admission,
        cancel: &CancelToken,
    ) -> Result<Arc<Track>, Interrupt<UserError>> {
        cancel.check()?;
        let file = match &overlay.source {
            LaneSource::File(file) => file.clone(),
            LaneSource::Derived { lanes, video } => {
                self.derive(lanes, video.as_ref(), admission, cancel)?
            }
        };
        let path = std::fs::canonicalize(&file).map_err(|e| {
            Interrupt::Failed(user_error("Cannot find the overlay file", &e, &file))
        })?;
        let decoder = (self.decoders)(&overlay.handler).ok_or_else(|| {
            Interrupt::Failed(
                UserError::new(
                    ErrorCode::UnknownHandler,
                    format!("No decoder is installed for '{}'", overlay.handler),
                )
                .at(HostPath::new(&path)),
            )
        })?;
        let options = decoder
            .spec()
            .options
            .validate(&overlay.options)
            .map_err(|error| Interrupt::Failed(invalid_option(&decoder, &error, &path)))?;
        let key = TrackKey {
            path,
            handler: overlay.handler.clone(),
            options: options.canonical(),
            code: decoder.code_hash(),
        };
        loop {
            let pending = match self.claim(&key) {
                Claim::Shared(track) => return Ok(track),
                Claim::Wait(pending) => match pending.wait(cancel)? {
                    Outcome::Done(result) => return result.map_err(Interrupt::Failed),
                    Outcome::Abandoned if admission == Admission::Reuse && key.code.is_some() => {
                        return Err(Interrupt::Failed(not_built(&key.path)));
                    }
                    Outcome::Abandoned | Outcome::Running => continue,
                },
                Claim::Open(pending) => pending,
            };
            return self.finish(&key, &decoder, &options, admission, &pending, cancel);
        }
    }

    /// Looks the key up and, when nobody has it, records that the caller opens it.
    fn claim(&self, key: &TrackKey) -> Claim {
        let mut slots = self.slots();
        match slots.get(key) {
            Some(Slot::Open(track)) => {
                if let Some(track) = track.upgrade()
                    && track.is_reusable()
                {
                    return Claim::Shared(track);
                }
            }
            Some(Slot::Loading(pending)) => return Claim::Wait(pending.clone()),
            None => {}
        }
        slots.retain(|_, slot| match slot {
            Slot::Open(track) => track.strong_count() > 0,
            Slot::Loading(_) => true,
        });
        let pending = Arc::new(Pending {
            state: Mutex::new(Outcome::Running),
            changed: Condvar::new(),
        });
        slots.insert(key.clone(), Slot::Loading(pending.clone()));
        Claim::Open(pending)
    }

    /// Opens the track this caller claimed and tells the waiters.
    fn finish(
        &self,
        key: &TrackKey,
        decoder: &Arc<dyn Decoder>,
        options: &Values,
        admission: Admission,
        pending: &Arc<Pending>,
        cancel: &CancelToken,
    ) -> Result<Arc<Track>, Interrupt<UserError>> {
        // Releases the claim even if loading panics, so waiters never wait on a dead opener.
        let mut claim = ClaimGuard {
            registry: self,
            key,
            pending,
            done: false,
        };
        // An open that was refused for want of a build is not an outcome for waiters: one that may compute takes over.
        let (result, slot, outcome) = match self.load(key, decoder, options, admission, cancel) {
            Ok(Loaded::Track(track)) => (
                Ok(track.clone()),
                Some(Slot::Open(Arc::downgrade(&track))),
                Outcome::Done(Ok(track)),
            ),
            Ok(Loaded::NotBuilt) => (
                Err(Interrupt::Failed(not_built(&key.path))),
                None,
                Outcome::Abandoned,
            ),
            Err(Interrupt::Failed(error)) => (
                Err(Interrupt::Failed(error.clone())),
                None,
                Outcome::Done(Err(error)),
            ),
            Err(Interrupt::Cancelled) => (Err(Interrupt::Cancelled), None, Outcome::Abandoned),
        };
        {
            let mut slots = self.slots();
            match slot {
                Some(slot) => slots.insert(key.clone(), slot),
                None => slots.remove(key),
            };
        }
        pending.set(outcome);
        claim.done = true;
        result
    }

    fn load(
        &self,
        key: &TrackKey,
        decoder: &Arc<dyn Decoder>,
        options: &Values,
        admission: Admission,
        cancel: &CancelToken,
    ) -> Result<Loaded, Interrupt<UserError>> {
        let artifact = self.cache_dir.as_ref().map(|dir| {
            dir.join(SCENE_FOLDER)
                .join(format!("{}.{SCENE_EXTENSION}", key.digest()))
        });
        load(
            decoder,
            &Request {
                key,
                options,
                artifact: artifact.as_deref(),
                admission,
                cache_bytes: self.scene_cache_bytes,
                tuning: self.tuning.clone(),
            },
            cancel,
        )
    }

    fn slots(&self) -> MutexGuard<'_, HashMap<TrackKey, Slot>> {
        self.slots.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

enum Claim {
    Shared(Arc<Track>),
    Wait(Arc<Pending>),
    Open(Arc<Pending>),
}

struct ClaimGuard<'a> {
    registry: &'a TrackRegistry,
    key: &'a TrackKey,
    pending: &'a Arc<Pending>,
    done: bool,
}

impl Drop for ClaimGuard<'_> {
    fn drop(&mut self) {
        if !self.done {
            self.registry.slots().remove(self.key);
            self.pending.set(Outcome::Abandoned);
        }
    }
}

/// A rejected option of `decoder`, named by its label, as a failure of the overlay file.
fn invalid_option(decoder: &Arc<dyn Decoder>, error: &UserError, path: &Path) -> UserError {
    let label = match &error.location {
        Some(Location::Field(key)) => decoder
            .spec()
            .options
            .fields
            .iter()
            .find(|field| field.key == *key)
            .map_or(key.as_str(), |field| field.label.as_str()),
        _ => "An option",
    };
    UserError::new(
        ErrorCode::OverlayInvalid,
        format!("{label}: {}", error.reason),
    )
    .at(HostPath::new(path))
}
