//! The recent workspaces, for the Start panel and File > Open Recent.

use crate::AppState;
use api_types::workspace::RecentWorkspace;
use axum::{Json, extract::State};

/// The workspace files opened or saved lately, newest first. They may no longer exist; opening one that is gone
/// removes it from the list.
pub(crate) async fn recent(State(state): State<AppState>) -> Json<Vec<RecentWorkspace>> {
    let files = std::sync::Arc::clone(&state.files);
    let list = tokio::task::spawn_blocking(move || files.recent())
        .await
        .unwrap_or_default();
    Json(
        list.into_iter()
            .map(|recent| RecentWorkspace {
                path: recent.path.to_string_lossy().into_owned(),
                used_ms: recent.used_ms,
            })
            .collect(),
    )
}
