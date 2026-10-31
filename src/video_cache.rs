//! How much memory the decoded-frame cache may use: the `playback.video_cache` setting, applied at startup and on
//! every change.

use crate::video_sources::VideoSources;
use playback::{CachePool, Registry};
use serde_json::Value;
use settings::{Settings, State};
use std::path::Path;
use std::sync::Arc;
use tokio::task::JoinHandle;

const MIB: u64 = 1024 * 1024;
/// Auto uses this share of the memory that is available at startup.
const AUTO_SHARE: u64 = 4;
/// Auto never gives less than this, however little memory is free.
const AUTO_MINIMUM: u64 = 256 * MIB;
/// Auto when the available memory cannot be read.
const AUTO_FALLBACK: u64 = 1024 * MIB;
const SETTING: &str = "playback.video_cache";

/// What the `playback.video_cache` setting says: `auto`, or a whole number of MiB.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VideoCache {
    /// A quarter of the memory that is available at startup, within limits.
    Auto,
    /// This many MiB, shared by every open video.
    Mib(u64),
}

impl VideoCache {
    /// The setting in `state`. The settings service only holds values that fit the setting, so anything else is a
    /// version mismatch that is logged and read as `auto`.
    fn of(state: &State) -> Self {
        match state.values().remove(SETTING) {
            Some(Value::String(name)) if name == "auto" => Self::Auto,
            Some(Value::Number(number)) if number.is_u64() => {
                Self::Mib(number.as_u64().unwrap_or_default())
            }
            other => {
                tracing::warn!("Unexpected {SETTING}: {other:?}; using auto");
                Self::Auto
            }
        }
    }
}

/// The cache allowance in bytes: the MiB given, or for `auto` a quarter of `available_memory`, at least 256 MiB,
/// and 1 GiB when the available memory is unknown.
pub fn allowance(setting: VideoCache, available_memory: Option<u64>) -> usize {
    let bytes = match setting {
        VideoCache::Mib(mib) => mib.saturating_mul(MIB),
        VideoCache::Auto => available_memory.map_or(AUTO_FALLBACK, |available| {
            (available / AUTO_SHARE).max(AUTO_MINIMUM)
        }),
    };
    usize::try_from(bytes).unwrap_or(usize::MAX)
}

/// Bytes of memory that can be used without swapping, where the system says so (Linux); `None` elsewhere.
pub fn available_memory() -> Option<u64> {
    parse_meminfo(&std::fs::read_to_string("/proc/meminfo").ok()?)
}

fn parse_meminfo(text: &str) -> Option<u64> {
    let line = text
        .lines()
        .find_map(|line| line.strip_prefix("MemAvailable:"))?;
    let kib: u64 = line.trim().strip_suffix("kB")?.trim().parse().ok()?;
    kib.checked_mul(1024)
}

/// The video sources of the app, sharing a cache allowance that follows the setting. Must be called inside a
/// Tokio runtime.
pub fn start(settings: &Settings, cache_dir: &Path) -> Arc<VideoSources> {
    let available = available_memory();
    let bytes = allowance(VideoCache::of(&settings.state()), available);
    tracing::info!("Video cache: {} MiB", bytes as u64 / MIB);
    let registry = Registry::new(CachePool::new(bytes));
    let videos = Arc::new(VideoSources::new(registry, cache_dir));
    follow(settings, Arc::clone(&videos), available);
    videos
}

/// Resizes the cache of `videos` whenever the setting changes, until `settings` is dropped. `available_memory` is
/// what `auto` was computed from at startup, so it stays the same size across changes.
pub fn follow(
    settings: &Settings,
    videos: Arc<VideoSources>,
    available_memory: Option<u64>,
) -> JoinHandle<()> {
    let mut changes = settings.subscribe();
    let mut applied = allowance(VideoCache::of(&settings.state()), available_memory);
    tokio::spawn(async move {
        while changes.changed().await.is_ok() {
            let setting = VideoCache::of(&changes.borrow_and_update());
            let bytes = allowance(setting, available_memory);
            if bytes == applied {
                continue;
            }
            applied = bytes;
            tracing::info!("Video cache: {} MiB", bytes as u64 / MIB);
            let videos = Arc::clone(&videos);
            // Shrinking frees frames, so it stays off the async workers.
            if let Err(error) =
                tokio::task::spawn_blocking(move || videos.registry().set_allowance(bytes)).await
            {
                tracing::error!("Cannot resize the video cache: {error}");
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const GIB: u64 = 1024 * MIB;

    #[test]
    fn auto_is_a_quarter_of_the_available_memory_with_a_floor_and_a_fallback() {
        let auto = VideoCache::Auto;
        assert_eq!(allowance(auto, Some(16 * GIB)) as u64, 4 * GIB);
        assert_eq!(allowance(auto, Some(GIB)) as u64, 256 * MIB, "the floor");
        assert_eq!(allowance(auto, None) as u64, GIB, "memory cannot be read");
        let fixed = VideoCache::Mib(512);
        assert_eq!(allowance(fixed, Some(16 * GIB)) as u64, 512 * MIB);
    }

    #[test]
    fn the_available_memory_is_read_from_the_kernel_report_in_kibibytes() {
        let report = "MemTotal:       32000000 kB\nMemFree:         1000000 kB\nMemAvailable:   18616000 kB\n";
        assert_eq!(parse_meminfo(report), Some(18_616_000 * 1024));
        assert_eq!(parse_meminfo("MemTotal: 1 kB\n"), None);
    }

    #[tokio::test]
    async fn a_saved_change_resizes_the_live_pool() {
        let folder = tempfile::tempdir().unwrap();
        let settings = Settings::load(&folder.path().join("default.json")).unwrap();
        let pool = CachePool::new(allowance(VideoCache::Auto, Some(4 * GIB)));
        let videos = Arc::new(VideoSources::new(
            Registry::new(pool.clone()),
            folder.path(),
        ));
        let task = follow(&settings, videos, Some(4 * GIB));
        let save = |cache: Value| {
            let mut all = settings.state().values();
            all.insert(SETTING.into(), cache);
            settings.replace(&all).unwrap();
        };
        let resized = async |expected: u64| {
            while pool.allowance() as u64 != expected {
                tokio::task::yield_now().await;
            }
        };

        save(json!(512));
        tokio::time::timeout(std::time::Duration::from_secs(10), resized(512 * MIB))
            .await
            .unwrap();
        save(json!("auto"));
        tokio::time::timeout(std::time::Duration::from_secs(10), resized(GIB))
            .await
            .unwrap();

        drop(settings);
        task.await.unwrap();
    }
}
