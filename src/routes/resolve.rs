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
use sessions::SessionId;
use std::{
    collections::{HashMap, VecDeque},
    sync::{Arc, Mutex, PoisonError},
};
use workspace::{Content, Environment, Watch, WorkspaceItem};

/// How many resolved items wait to be added per session. A resolve that finishes as its dialog is cancelled leaves
/// one that is never added; the oldest of that session makes room. The session id is only a partition, so one
/// session's abandoned dialogs never evict another's items: it is not a credential, and the callers of one backend
/// are trusted collaborators. The resolve route accepts only a session the backend holds.
const MAX_STAGED: usize = 4;

/// Items resolved by `POST /api/resolvers/{id}/resolve` and not yet added, by the session that asked. The item's id
/// is the ticket, redeemed only through a seat of that session.
#[derive(Default)]
pub struct Staged(Mutex<HashMap<String, VecDeque<Resolved>>>);

/// A staged item: what it resolved to, and what it was read from as it was before, which is watched once the item is
/// added, so a change made while the dialog was open is noticed.
pub(crate) type Resolved = (WorkspaceItem, Content, Option<Watch>);

impl Staged {
    /// Holds a resolved item for `session`; with [`MAX_STAGED`] of its items already held, its oldest is dropped.
    /// The items of sessions that `is_live` no longer names are dropped too, so what is held stays bounded by the
    /// live sessions.
    pub(crate) fn put(
        &self,
        session: &str,
        item: WorkspaceItem,
        content: Content,
        watch: Option<Watch>,
        is_live: impl Fn(&str) -> bool,
    ) {
        let mut held = self.0.lock().unwrap_or_else(PoisonError::into_inner);
        held.retain(|key, _| key == session || is_live(key));
        let queue = held.entry(session.to_owned()).or_default();
        if queue.len() == MAX_STAGED {
            queue.pop_front();
        }
        queue.push_back((item, content, watch));
    }

    /// Hands over the item with this id once, to the session that staged it and no other.
    pub(crate) fn take(&self, session: &str, id: &ItemId) -> Option<Resolved> {
        let mut held = self.0.lock().unwrap_or_else(PoisonError::into_inner);
        let queue = held.get_mut(session)?;
        let at = queue.iter().position(|(item, ..)| item.id == *id)?;
        let taken = queue.remove(at);
        if queue.is_empty() {
            held.remove(session);
        }
        taken
    }
}

/// Checks the settings, resolves them on a blocking thread and stages the result; returns the item's id. Dropping
/// the returned future, as the server does when the client closes the request, cancels the resolver, and nothing is
/// staged.
async fn stage(
    handlers: Arc<Handlers>,
    staged: &Staged,
    (session, is_live): (&str, impl Fn(&str) -> bool),
    resolver: &str,
    settings: Settings,
) -> Result<ItemId, Interrupt<UserError>> {
    let item =
        workspace::intake::playlist(resolver, settings, &handlers).map_err(Interrupt::Failed)?;
    let cancel = CancelToken::new();
    let _cancel_when_dropped = CancelOnDrop(cancel.clone());
    let job = {
        let item = item.clone();
        tokio::task::spawn_blocking(move || {
            Watch::read(&item.recipe, &handlers, &cancel, || {
                item.resolve(&handlers, &Environment::of_process(), &cancel)
            })
        })
    };
    let (content, watch) = job.await.unwrap_or_else(|error| {
        tracing::error!("Finding the playlists of {resolver} failed: {error}");
        (
            Err(Interrupt::Failed(UserError::new(
                ErrorCode::Internal,
                "Finding the playlists stopped unexpectedly",
            ))),
            None,
        )
    });
    let content = content?;
    let id = item.id;
    staged.put(session, item, content, watch, is_live);
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
    let live = |key: &str| {
        key.parse::<SessionId>()
            .is_ok_and(|id| state.sessions.subscribe(id).is_some())
    };
    if !live(&request.session) {
        return Err(refuse(
            StatusCode::NOT_FOUND,
            &UserError::new(ErrorCode::Internal, "That session no longer exists"),
        ));
    }
    match stage(
        handlers,
        &state.staged,
        (&request.session, live),
        &id,
        request.settings,
    )
    .await
    {
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

    fn post(session: &str, id: &str, settings: serde_json::Value) -> Request<Body> {
        Request::builder()
            .method("POST")
            .uri(format!("/api/resolvers/{id}/resolve"))
            .header("content-type", "application/json")
            .body(Body::from(
                json!({ "session": session, "settings": settings }).to_string(),
            ))
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
    async fn a_resolve_names_the_item_a_failure_the_setting_and_an_unknown_session_is_refused() {
        let test = TestApp::new();
        let owner = test
            .state
            .sessions
            .create(
                true,
                sessions::CursorOptions {
                    show: true,
                    share: true,
                },
            )
            .unwrap();
        let session = owner.session().to_string();
        let folder = mot_folder();

        let (status, body) = test
            .send(post(
                &session,
                "mot_challenge",
                json!({ "root": folder.path() }),
            ))
            .await;
        assert_eq!(status, StatusCode::OK);
        assert!(json(&body)["item"].is_string());

        let (status, body) = test.send(post(&session, "mot_challenge", json!({}))).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(json(&body)["location"], "root");
        let missing = folder.path().join("gone");
        let (status, _) = test
            .send(post(&session, "mot_challenge", json!({ "root": missing })))
            .await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        let (status, _) = test.send(post(&session, "no_such", json!({}))).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        let unheld = json!({ "root": folder.path() });
        for session in ["s1", "0000000000000000"] {
            let (status, _) = test
                .send(post(session, "mot_challenge", unheld.clone()))
                .await;
            assert_eq!(status, StatusCode::NOT_FOUND, "{session}");
        }
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
            async move {
                stage(
                    handlers,
                    &staged,
                    ("s1", |_: &str| true),
                    "waiting",
                    Settings::new(),
                )
                .await
            }
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
    fn each_session_keeps_its_latest_four_items_only_it_takes_them_once_and_a_gone_session_loses_them()
     {
        let staged = Staged::default();
        let folder = mot_folder();
        let handlers = Handlers::built_in();
        let stage_one = |session: &str, live: &[&str]| {
            let mut settings = Settings::new();
            settings.insert("root".into(), folder.path().display().to_string().into());
            let item = workspace::intake::playlist("mot_challenge", settings, &handlers).unwrap();
            let content = item
                .resolve(&handlers, &Environment::of_process(), &CancelToken::new())
                .ok()
                .unwrap();
            let id = item.id;
            staged.put(session, item, content, None, |key| live.contains(&key));
            id
        };
        let first: Vec<ItemId> = (0..=MAX_STAGED)
            .map(|_| stage_one("a", &["a", "b"]))
            .collect();
        let other = stage_one("b", &["a", "b"]);

        assert!(
            staged.take("a", &first[0]).is_none(),
            "its oldest made room"
        );
        assert!(staged.take("a", &other).is_none(), "another session's item");
        assert!(
            staged.take("b", &first[1]).is_none(),
            "not another session's"
        );
        assert!(staged.take("a", &first[1]).is_some());
        assert!(staged.take("a", &first[1]).is_none(), "handed over once");
        // Staging for another session drops the queues of those no longer live: b is gone, a is not.
        let kept = stage_one("a", &["a"]);
        assert!(staged.take("b", &other).is_none(), "a gone session's item");
        stage_one("c", &["c"]);
        assert!(staged.take("a", &kept).is_none(), "a is gone now");
    }
}
