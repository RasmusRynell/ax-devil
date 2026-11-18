//! The Clear Cache route: removes the cache files ax-devil owns from the folder this backend runs with.

use crate::{AppState, Failure, disk_cache, rejected};
use api_types::CacheCleared;
use axum::{Json, extract::State, http::StatusCode};

/// Removes the frame indexes, overlay artifacts and thumbnails from the cache folder and answers with what it
/// removed. Files in use are not protected: they are rebuilt on the next open.
pub(super) async fn clear(State(state): State<AppState>) -> Result<Json<CacheCleared>, Failure> {
    let folder = state.cache_dir;
    let removed = tokio::task::spawn_blocking(move || {
        let found = disk_cache::scan(&folder)?;
        let mut cleared = disk_cache::clear(&found.locations);
        let skipped = found
            .skipped
            .iter()
            .map(|folder| format!("{}: not a plain folder, skipped", folder.display()));
        cleared.failures.extend(skipped);
        Ok::<_, String>(cleared)
    })
    .await
    .map_err(|error| {
        tracing::error!("Clearing the cache failed: {error}");
        rejected(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Clearing the cache stopped unexpectedly".into(),
        )
    })?
    .map_err(|reason| rejected(StatusCode::INTERNAL_SERVER_ERROR, reason))?;
    Ok(Json(CacheCleared {
        summary: removed.summary(),
        failures: removed.failures,
    }))
}
