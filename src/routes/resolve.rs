//! Resolving playlists before the item exists: the Add Playlist dialog resolves first and adds the item only when
//! that succeeds, so a failure or a cancel leaves nothing behind.

use super::{
    CancelOnDrop,
    handlers::{Refusal, refuse},
};
use crate::AppState;
use api_types::handlers::{ResolveRequest, ResolvedInfo};
use axum::{
    Json,
    extract::{Path, State},
    http::StatusCode,
};
use foundation::{CancelToken, ErrorCode, Interrupt, ItemId, UserError};
use handler_api::Settings;
use handlers::Handlers;
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex, PoisonError},
};
use workspace::{Content, WorkspaceItem};

/// How many resolved items wait to be added. A resolve that finishes as its dialog is cancelled leaves one that is
/// never added; the oldest makes room.
const MAX_STAGED: usize = 4;

/// Items resolved by `POST /api/resolvers/{id}/resolve` and not yet added. The item's id is the ticket.
#[derive(Default)]
pub struct Staged(Mutex<VecDeque<(WorkspaceItem, Content)>>);

impl Staged {
    /// Holds a resolved item; with [`MAX_STAGED`] already held, the oldest is dropped.
    pub(crate) fn put(&self, item: WorkspaceItem, content: Content) {
        let mut held = self.0.lock().unwrap_or_else(PoisonError::into_inner);
        if held.len() == MAX_STAGED {
            held.pop_front();
        }
        held.push_back((item, content));
    }

    /// Hands over the item with this id, once.
    pub(crate) fn take(&self, id: &ItemId) -> Option<(WorkspaceItem, Content)> {
        let mut held = self.0.lock().unwrap_or_else(PoisonError::into_inner);
        let at = held.iter().position(|(item, _)| item.id == *id)?;
        held.remove(at)
    }
}

/// Checks the settings, resolves them on a blocking thread and stages the result; returns the item's id. Dropping
/// the returned future, as the server does when the client closes the request, cancels the resolver, and nothing is
/// staged.
async fn stage(
    handlers: Arc<Handlers>,
    staged: &Staged,
    resolver: &str,
    settings: Settings,
) -> Result<ItemId, Interrupt<UserError>> {
    let item =
        workspace::intake::playlist(resolver, settings, &handlers).map_err(Interrupt::Failed)?;
    let cancel = CancelToken::new();
    let _cancel_when_dropped = CancelOnDrop(cancel.clone());
    let job = {
        let item = item.clone();
        tokio::task::spawn_blocking(move || item.recipe.resolve(&item.id, &handlers, &cancel))
    };
    let content = job.await.unwrap_or_else(|error| {
        tracing::error!("Finding the playlists of {resolver} failed: {error}");
        Err(Interrupt::Failed(UserError::new(
            ErrorCode::Internal,
            "Finding the playlists stopped unexpectedly",
        )))
    })?;
    let id = item.id;
    staged.put(item, content);
    Ok(id)
}

/// Resolves a playlist item from the settings and holds it for `addResolved`. The caller cancels by aborting its
/// request.
pub(crate) async fn resolve(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(request): Json<ResolveRequest>,
) -> Result<Json<ResolvedInfo>, Refusal> {
    let handlers = state.plugins.handlers();
    if handlers.resolver(&id).is_none() {
        return Err(refuse(
            StatusCode::NOT_FOUND,
            &UserError::new(ErrorCode::UnknownHandler, "No such playlist resolver"),
        ));
    }
    match stage(handlers, &state.staged, &id, request.settings).await {
        Ok(item) => Ok(Json(ResolvedInfo { item })),
        Err(Interrupt::Failed(error)) => Err(refuse(StatusCode::UNPROCESSABLE_ENTITY, &error)),
        // Only a dropped request cancels a resolve, so nobody reads this answer.
        Err(Interrupt::Cancelled) => Err(refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            &UserError::new(ErrorCode::Internal, "The request was cancelled"),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{TestApp, json};
    use axum::{body::Body, http::Request};
    use handler_api::{Form, PlaylistResolver, ResolvedPlaylists, ResolverSpec, Values};
    use serde_json::json;
    use std::{
        sync::{
            LazyLock,
            atomic::{AtomicBool, Ordering},
        },
        time::{Duration, Instant},
    };

    fn post(id: &str, settings: serde_json::Value) -> Request<Body> {
        Request::builder()
            .method("POST")
            .uri(format!("/api/resolvers/{id}/resolve"))
            .header("content-type", "application/json")
            .body(Body::from(json!({ "settings": settings }).to_string()))
            .unwrap()
    }

    fn mot_folder() -> tempfile::TempDir {
        let folder = tempfile::tempdir().unwrap();
        let dir = folder.path().join("SEQ-01");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("seqinfo.ini"),
            "[Sequence]\nframeRate=25\nseqLength=10\nimWidth=64\nimHeight=48\n",
        )
        .unwrap();
        folder
    }

    #[tokio::test]
    async fn a_resolve_names_the_item_and_a_failure_names_the_setting() {
        let test = TestApp::new();
        let folder = mot_folder();

        let (status, body) = test
            .send(post("mot_challenge", json!({ "root": folder.path() })))
            .await;
        assert_eq!(status, StatusCode::OK);
        assert!(json(&body)["item"].is_string());

        let (status, body) = test.send(post("mot_challenge", json!({}))).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(json(&body)["location"], "root");
        let missing = folder.path().join("gone");
        let (status, _) = test
            .send(post("mot_challenge", json!({ "root": missing })))
            .await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        let (status, _) = test.send(post("no_such", json!({}))).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
    }

    /// A resolver that resolves until it is cancelled and says whether it saw that.
    #[derive(Default)]
    struct Waiting {
        started: AtomicBool,
        cancelled: AtomicBool,
    }

    static WAITING_SPEC: LazyLock<ResolverSpec> = LazyLock::new(|| ResolverSpec {
        id: "waiting".into(),
        plugin: "waiting".into(),
        name: "Waiting".into(),
        description: "Resolves until cancelled.".into(),
        form: Form::default(),
    });

    impl PlaylistResolver for Waiting {
        fn spec(&self) -> &ResolverSpec {
            &WAITING_SPEC
        }

        fn resolve(
            &self,
            _settings: &Values,
            cancel: &CancelToken,
        ) -> Result<ResolvedPlaylists, Interrupt<UserError>> {
            self.started.store(true, Ordering::Release);
            let deadline = Instant::now() + Duration::from_secs(10);
            while Instant::now() < deadline {
                if cancel.is_cancelled() {
                    self.cancelled.store(true, Ordering::Release);
                    return Err(Interrupt::Cancelled);
                }
                std::thread::sleep(Duration::from_millis(1));
            }
            Ok(ResolvedPlaylists::default())
        }
    }

    async fn until(what: &AtomicBool) -> bool {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !what.load(Ordering::Acquire) {
            if Instant::now() > deadline {
                return false;
            }
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
        true
    }

    #[tokio::test]
    async fn a_dropped_request_cancels_the_resolver_and_stages_nothing() {
        let resolver = Arc::new(Waiting::default());
        let handlers = Arc::new(
            Handlers::built_in().with_resolvers([resolver.clone() as Arc<dyn PlaylistResolver>]),
        );
        let staged = Arc::new(Staged::default());
        let request = tokio::spawn({
            let staged = staged.clone();
            async move { stage(handlers, &staged, "waiting", Settings::new()).await }
        });
        assert!(until(&resolver.started).await, "the resolver started");

        request.abort();

        assert!(
            until(&resolver.cancelled).await,
            "the resolver saw the cancel"
        );
        assert!(staged.0.lock().unwrap().is_empty());
    }

    #[test]
    fn only_the_latest_four_items_wait_and_each_is_handed_over_once() {
        let staged = Staged::default();
        let folder = mot_folder();
        let handlers = Handlers::built_in();
        let ids: Vec<ItemId> = (0..=MAX_STAGED)
            .map(|_| {
                let mut settings = Settings::new();
                settings.insert("root".into(), folder.path().display().to_string().into());
                let item =
                    workspace::intake::playlist("mot_challenge", settings, &handlers).unwrap();
                let content = item
                    .recipe
                    .resolve(&item.id, &handlers, &CancelToken::new())
                    .ok()
                    .unwrap();
                let id = item.id;
                staged.put(item, content);
                id
            })
            .collect();

        assert!(staged.take(&ids[0]).is_none(), "the oldest made room");
        assert!(staged.take(&ids[1]).is_some());
        assert!(
            staged.take(&ids[1]).is_none(),
            "an item is handed over once"
        );
    }
}
