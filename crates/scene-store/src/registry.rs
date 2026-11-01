//! Opening tracks and sharing them between viewers.

use std::{
    collections::{BTreeMap, HashMap},
    fmt::Write as _,
    path::{Path, PathBuf},
    sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError, Weak},
    time::Duration,
};

use foundation::{CancelToken, FrameSize, Interrupt, UserError};
use handlers::Handlers;
use playlist_resolvers::OverlayRef;
use scene_decoders::FileSceneDecoder;
use serde_json::Value as Json;
use sha2::{Digest, Sha256};

use crate::{
    io::user_error,
    load::{Request, load},
    track::Track,
};

/// How many bytes of decoded Scenes each track keeps in memory by default.
pub const SCENE_CACHE_BYTES: usize = 32 << 20;

/// How often a waiting open looks at its cancel token.
const WAIT_SLICE: Duration = Duration::from_millis(20);

/// What identifies an opened track: the same key shares one decode.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct TrackKey {
    /// The overlay file, canonical.
    pub path: PathBuf,
    /// The decoder handler id.
    pub handler: String,
    /// The decoder options as canonical JSON: object keys sorted at every depth.
    pub options: String,
    /// The decoded size of the video's frames.
    pub frame_size: FrameSize,
}

impl TrackKey {
    /// The artifact file name stem: SHA-256 of the key, hex.
    fn digest(&self) -> String {
        let mut hash = Sha256::new();
        for part in [
            self.path.as_os_str().as_encoded_bytes(),
            self.handler.as_bytes(),
            self.options.as_bytes(),
            format!("{}x{}", self.frame_size.width, self.frame_size.height).as_bytes(),
        ] {
            hash.update(part);
            hash.update([0]);
        }
        hash.finalize().iter().fold(String::new(), |mut hex, byte| {
            let _ = write!(hex, "{byte:02x}");
            hex
        })
    }
}

type DecoderLookup = dyn Fn(&str) -> Option<Arc<dyn FileSceneDecoder>> + Send + Sync;

/// Opens and shares tracks.
pub struct TrackRegistry {
    cache_dir: Option<PathBuf>,
    decoders: Arc<DecoderLookup>,
    scene_cache_bytes: usize,
    slots: Mutex<HashMap<TrackKey, Slot>>,
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
    /// A registry decoding with `handlers`, keeping artifacts under `cache_dir` when it is given. Without a cache
    /// folder artifacts live in unnamed temporary files and are not reused across opens.
    pub fn new(handlers: Handlers, cache_dir: Option<&Path>) -> Self {
        Self::with_decoders(
            move |handler| handlers.file_decoder(handler).cloned(),
            cache_dir,
        )
    }

    /// A registry that finds decoders with `lookup`.
    pub(crate) fn with_decoders(
        lookup: impl Fn(&str) -> Option<Arc<dyn FileSceneDecoder>> + Send + Sync + 'static,
        cache_dir: Option<&Path>,
    ) -> Self {
        Self {
            cache_dir: cache_dir.map(Path::to_path_buf),
            decoders: Arc::new(lookup),
            scene_cache_bytes: SCENE_CACHE_BYTES,
            slots: Mutex::new(HashMap::new()),
        }
    }

    /// Sets how many bytes of decoded Scenes each track opened from now on keeps in memory.
    #[must_use]
    pub fn with_scene_cache(mut self, bytes: usize) -> Self {
        self.scene_cache_bytes = bytes;
        self
    }

    /// Blocking. Shares an open track with the same key; else loads a valid artifact or decodes the file and writes
    /// one.
    ///
    /// Concurrent opens of one key decode once: the others wait, and one of them takes over if the opener is
    /// cancelled.
    pub fn open(
        &self,
        overlay: &OverlayRef,
        frame_size: FrameSize,
        cancel: &CancelToken,
    ) -> Result<Arc<Track>, Interrupt<UserError>> {
        cancel.check()?;
        let path = std::fs::canonicalize(&overlay.path).map_err(|e| {
            Interrupt::Failed(user_error(
                "Cannot find the overlay file",
                &e,
                &overlay.path,
            ))
        })?;
        let key = TrackKey {
            path,
            handler: overlay.handler.clone(),
            options: canonical_json(&overlay.options),
            frame_size,
        };
        loop {
            let pending = match self.claim(&key) {
                Claim::Shared(track) => return Ok(track),
                Claim::Wait(pending) => match pending.wait(cancel)? {
                    Outcome::Done(result) => return result.map_err(Interrupt::Failed),
                    Outcome::Abandoned | Outcome::Running => continue,
                },
                Claim::Open(pending) => pending,
            };
            return self.finish(&key, overlay, &pending, cancel);
        }
    }

    /// Looks the key up and, when nobody has it, records that the caller opens it.
    fn claim(&self, key: &TrackKey) -> Claim {
        let mut slots = self.slots();
        match slots.get(key) {
            Some(Slot::Open(track)) => {
                if let Some(track) = track.upgrade()
                    && !track.is_damaged()
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
        overlay: &OverlayRef,
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
        let result = self.load(key, overlay, cancel);
        let (slot, outcome) = match &result {
            Ok(track) => (
                Some(Slot::Open(Arc::downgrade(track))),
                Outcome::Done(Ok(track.clone())),
            ),
            Err(Interrupt::Failed(error)) => (None, Outcome::Done(Err(error.clone()))),
            Err(Interrupt::Cancelled) => (None, Outcome::Abandoned),
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
        overlay: &OverlayRef,
        cancel: &CancelToken,
    ) -> Result<Arc<Track>, Interrupt<UserError>> {
        let decoder = (self.decoders)(&key.handler).ok_or_else(|| {
            Interrupt::Failed(UserError::new(
                format!("No decoder is installed for '{}'", key.handler),
                key.path.display().to_string(),
            ))
        })?;
        let artifact = self
            .cache_dir
            .as_ref()
            .map(|dir| dir.join("scenes").join(format!("{}.axscene", key.digest())));
        load(
            decoder.as_ref(),
            &Request {
                key,
                options: &overlay.options,
                artifact: artifact.as_deref(),
                cache_bytes: self.scene_cache_bytes,
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

/// `options` as JSON text with the keys of every object in order, so equal options make equal keys.
pub(crate) fn canonical_json(options: &serde_json::Map<String, Json>) -> String {
    fn sorted(value: &Json) -> Json {
        match value {
            Json::Object(map) => Json::Object(
                map.iter()
                    .collect::<BTreeMap<_, _>>()
                    .into_iter()
                    .map(|(key, value)| (key.clone(), sorted(value)))
                    .collect(),
            ),
            Json::Array(items) => Json::Array(items.iter().map(sorted).collect()),
            other => other.clone(),
        }
    }
    sorted(&Json::Object(options.clone())).to_string()
}
