//! The recent workspaces, for the Start panel and File > Open Recent.

use crate::{AppState, access::Principal};
use api_types::workspace::RecentWorkspace;
use axum::{Json, extract::State};

/// The workspace files opened or saved lately, newest first. They may no longer exist; opening one that is gone
/// removes it from the list.
pub(crate) async fn recent(
    principal: Principal,
    State(state): State<AppState>,
) -> Json<Vec<RecentWorkspace>> {
    let files = std::sync::Arc::clone(&state.files);
    let list = tokio::task::spawn_blocking(move || files.recent())
        .await
        .unwrap_or_default();
    let home = principal.full_access().then(std::env::home_dir).flatten();
    Json(
        list.into_iter()
            .map(|recent| RecentWorkspace {
                folder: recent.display_folder(home.as_deref()),
                name: recent
                    .recovers
                    .as_ref()
                    .unwrap_or(&recent.path)
                    .file_name()
                    .map_or_else(String::new, |name| name.to_string_lossy().into_owned()),
                path: recent.path.to_string_lossy().into_owned(),
                used_ms: recent.used_ms,
            })
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{session::WorkspaceStorage, test_support::TestApp};
    use workspace_files::{Recents, RecoveryFolder};

    #[tokio::test]
    async fn a_recent_uses_only_its_requesting_audiences_home_authority() {
        let app = TestApp::new();
        let folder = tempfile::tempdir().unwrap();
        let recents = Recents::new(folder.path().join("recent.json"));
        // No workspace file is read or written; the home belongs to scripts/isolated.
        let home = std::env::home_dir().unwrap();
        recents
            .record(&home.join("reviews/gate.ax-devil.workspace"), 1)
            .unwrap();
        let state = app.state.clone().with_workspace_storage(WorkspaceStorage {
            recents,
            recovery: RecoveryFolder::new(folder.path().join("unsaved")),
        });
        let Json(local) = recent(Principal::Owner { full: true }, State(state.clone())).await;
        let Json(remote) = recent(Principal::Owner { full: false }, State(state)).await;
        assert_eq!(local[0].folder, "~/reviews");
        assert_eq!(remote[0].folder, home.join("reviews").to_string_lossy());
        assert_eq!(
            local[0].path, remote[0].path,
            "the existing path response is unchanged"
        );
    }
}
