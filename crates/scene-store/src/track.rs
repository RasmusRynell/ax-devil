//! A decoded overlay file: its sample keys in memory, its Scenes in the artifact.

use std::{
    fs::File,
    path::PathBuf,
    sync::{
        Arc, Mutex, PoisonError,
        atomic::{AtomicBool, Ordering},
    },
};

use foundation::{ErrorCode, HostPath, UserError};
use overlay_sync::SampleKeys;
use scene::{Coverage, Fields, FilterPolicy, Scene, serial};
use scene_decoders::LoadWarning;

use crate::{
    format::Layout,
    io::{names_file, read_exact_at},
    lru::SceneCache,
    summary::Restored,
};

/// One decoded overlay file: its sample keys in memory and its Scenes behind an artifact.
///
/// Memory is the keys, a few bytes per record, and a byte-bounded cache of recently used Scenes; everything else
/// stays on disk.
pub struct Track {
    source: PathBuf,
    keys: SampleKeys,
    coverage: Coverage,
    warnings: Vec<LoadWarning>,
    filter: FilterPolicy,
    metadata: Fields,
    /// The handle that wrote or validated the artifact; positional reads leave it shared.
    artifact: File,
    /// Where the artifact is stored, if it is.
    artifact_path: Option<PathBuf>,
    /// Set when a record failed its check; the registry then stops sharing this track.
    damaged: AtomicBool,
    boundaries: Vec<u64>,
    crcs: Vec<u32>,
    order: Vec<u32>,
    cache: Mutex<SceneCache>,
}

impl Track {
    pub(crate) fn new(
        source: PathBuf,
        artifact: File,
        artifact_path: Option<PathBuf>,
        layout: Layout,
        restored: Restored,
        cache_bytes: usize,
    ) -> Self {
        Self {
            source,
            keys: layout.keys,
            coverage: restored.coverage,
            warnings: restored.warnings,
            filter: restored.filter,
            metadata: restored.metadata,
            artifact,
            artifact_path,
            damaged: AtomicBool::new(false),
            boundaries: layout.boundaries,
            crcs: layout.crcs,
            order: layout.order,
            cache: Mutex::new(SceneCache::new(cache_bytes)),
        }
    }

    /// The sorted keys of the samples.
    pub fn keys(&self) -> &SampleKeys {
        &self.keys
    }

    /// What the file says about frames it has no Scene for.
    pub fn coverage(&self) -> Coverage {
        self.coverage
    }

    /// Problems the decoder reported.
    pub fn warnings(&self) -> &[LoadWarning] {
        &self.warnings
    }

    /// The class filters the file's content calls for.
    pub fn filter(&self) -> &FilterPolicy {
        &self.filter
    }

    /// What the file says about itself.
    pub fn metadata(&self) -> &Fields {
        &self.metadata
    }

    /// The Scene of sample `index`; reads the artifact through the handle that wrote or validated it, and keeps
    /// recent Scenes in a bounded cache.
    pub fn scene(&self, index: usize) -> Result<Arc<Scene>, UserError> {
        if let Some(scene) = self.cache().get(index) {
            return Ok(scene);
        }
        // Read and decode outside the lock; two threads missing the same Scene both read it and one copy is kept.
        let scene = Arc::new(self.read(index)?);
        self.cache().insert(index, scene.clone());
        Ok(scene)
    }

    /// Whether a record of the artifact failed its check, so the artifact is gone and a new open decodes again.
    pub(crate) fn is_damaged(&self) -> bool {
        self.damaged.load(Ordering::Acquire)
    }

    /// How many bytes of Scenes the cache holds.
    #[cfg(test)]
    pub(crate) fn cached_bytes(&self) -> usize {
        self.cache().used()
    }

    fn cache(&self) -> std::sync::MutexGuard<'_, SceneCache> {
        self.cache.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Marks the artifact damaged and unlinks it, so the next open decodes the file again. Readers that hold the
    /// file keep reading it; a path that names another file now (a newer artifact) is left alone.
    fn invalidate(&self) {
        if self.damaged.swap(true, Ordering::AcqRel) {
            return;
        }
        let Some(path) = self.artifact_path.as_deref() else {
            return;
        };
        if names_file(path, &self.artifact)
            && let Err(error) = std::fs::remove_file(path)
        {
            tracing::warn!(artifact = %path.display(), %error, "could not remove a damaged scene artifact");
        }
    }

    fn read(&self, index: usize) -> Result<Scene, UserError> {
        let fail = |reason: String| {
            UserError::new(
                ErrorCode::FileUnreadable,
                format!(
                    "The scene cache of this overlay is damaged at sample {index} ({reason}); open the \
                     overlay again"
                ),
            )
            .at(HostPath::new(&self.source))
        };
        if index >= self.keys.len() {
            return Err(fail("no such sample".to_string()));
        }
        let damaged = |reason: String| {
            self.invalidate();
            fail(reason)
        };
        let record = self
            .order
            .get(index)
            .map_or(index, |&record| record as usize);
        let (start, end) = (self.boundaries[record], self.boundaries[record + 1]);
        // `Layout` checked that no record is longer than MAX_RECORD_LEN.
        let mut bytes = vec![0_u8; (end - start) as usize];
        read_exact_at(&self.artifact, &mut bytes, start).map_err(|e| damaged(e.to_string()))?;
        if crc32fast::hash(&bytes) != self.crcs[record] {
            return Err(damaged("checksum mismatch".to_string()));
        }
        serial::decode(&bytes).map_err(|e| damaged(e.to_string()))
    }
}
