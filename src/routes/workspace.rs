//! Routes for the workspace: its state, adding items through the intake, and the stream of state changes.
//!
//! Changes reach the frontend as server-sent events, not a WebSocket: the traffic is one-way, small and needs only
//! reconnection, which `EventSource` does by itself. Every event carries the whole state, so a client that connects
//! or reconnects needs nothing else.

use super::CancelOnDrop;
use crate::{AppState, failure};
use api_types::{
    ApiError,
    content::{ContentInfo, EntryInfo},
    workspace::{
        AddItemsRequest, AddPlaylistRequest, HiddenRequest, HiddenTarget, ItemState, NewItem,
        ResolutionState, RowInfo, RowKindWire, RowStatusInfo, ServerEvent, WorkspaceState,
    },
};
use axum::{
    Json, Router,
    extract::State,
    http::StatusCode,
    response::sse::{Event, KeepAlive, Sse},
    routing::{get, post},
};
use foundation::{CancelToken, Interrupt};
use std::{convert::Infallible, path::Path};
use tokio_stream::{Stream, StreamExt, wrappers::WatchStream};
use workspace::{Content, Entry, intake::NewOverlay};
use workspace_store::{HiddenRef, Resolution, RowKind, RowStatus, StoreState};

/// The workspace routes.
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/workspace", get(get_workspace))
        .route("/api/workspace/items", post(add_items))
        .route("/api/workspace/playlists", post(add_playlist))
        .route("/api/workspace/hidden", post(set_hidden))
        .route("/api/events", get(events))
}

fn entry_info(entry: &Entry) -> EntryInfo {
    EntryInfo {
        id: entry.id.to_string(),
        label: entry.label.clone(),
        lanes: entry.lane_labels().into_iter().map(str::to_owned).collect(),
    }
}

fn row_info(row: workspace_store::Row) -> RowInfo {
    RowInfo {
        key: row.key,
        kind: match row.kind {
            RowKind::Item => RowKindWire::Item,
            RowKind::Entry => RowKindWire::Entry,
            RowKind::Lane => RowKindWire::Lane,
        },
        label: row.label,
        status: match row.status {
            RowStatus::Pending => RowStatusInfo::Pending,
            RowStatus::Unavailable { reason } => RowStatusInfo::Unavailable { reason },
            RowStatus::Ready => RowStatusInfo::Ready,
        },
        hidden: row.hidden,
        entry: row.entry.map(|id| id.to_string()),
    }
}

fn project(state: &StoreState) -> WorkspaceState {
    WorkspaceState {
        name: state.workspace.name.clone(),
        items: state
            .workspace
            .items
            .iter()
            .map(|item| ItemState {
                id: item.id.to_string(),
                label: item.label.clone(),
                open: state.open.contains(&item.id),
                resolution: match state.resolutions.get(&item.id) {
                    Some(Resolution::Resolved(content)) => ResolutionState::Resolved {
                        content: match content {
                            Content::SeekableVideo(video) => ContentInfo::SeekableVideo {
                                id: video.entry.id.to_string(),
                                display_name: content.display_name().to_owned(),
                                entry: entry_info(&video.entry),
                            },
                            Content::Playlist(playlist) => ContentInfo::Playlist {
                                id: playlist.id.to_string(),
                                display_name: playlist.display_name.clone(),
                                entries: playlist.entries.iter().map(entry_info).collect(),
                            },
                        },
                    },
                    Some(Resolution::Failed(error)) => ResolutionState::Failed {
                        reason: error.reason.clone(),
                        location: error.location.clone(),
                    },
                    Some(Resolution::Pending) | None => ResolutionState::Pending,
                },
            })
            .collect(),
        rows: workspace_store::rows(state)
            .into_iter()
            .map(row_info)
            .collect(),
    }
}

async fn get_workspace(State(state): State<AppState>) -> Json<WorkspaceState> {
    Json(project(&state.store.snapshot()))
}

/// Adds the items in the request, all or none: the intake refuses the whole request when it refuses any entry.
async fn add_items(
    State(state): State<AppState>,
    Json(request): Json<AddItemsRequest>,
) -> Result<Json<WorkspaceState>, (StatusCode, Json<ApiError>)> {
    let handlers = &state.handlers;
    let items = request
        .items
        .iter()
        .map(|new| match new {
            NewItem::Video { path, overlays } => {
                let overlays: Vec<NewOverlay> = overlays
                    .iter()
                    .map(|overlay| NewOverlay {
                        path: overlay.path.clone().into(),
                        handler: overlay.handler.clone(),
                    })
                    .collect();
                workspace::intake::video(Path::new(path), &overlays, handlers)
            }
            NewItem::Playlist {
                resolver,
                settings,
                label,
            } => workspace::intake::playlist(resolver, settings.clone(), label.clone(), handlers),
        })
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| {
            failure(
                StatusCode::UNPROCESSABLE_ENTITY,
                &error.location,
                error.reason,
            )
        })?;
    state.store.add_items(items, request.open);
    Ok(Json(project(&state.store.snapshot())))
}

/// Resolves the playlist first and adds it, already resolved and opening, only when that works, so the dialog that
/// asked stays open with the reason otherwise. Resolving stops when the client drops the request.
async fn add_playlist(
    State(state): State<AppState>,
    Json(request): Json<AddPlaylistRequest>,
) -> Result<Json<WorkspaceState>, (StatusCode, Json<ApiError>)> {
    let refused = |error: foundation::UserError| {
        failure(
            StatusCode::UNPROCESSABLE_ENTITY,
            &error.location,
            error.reason,
        )
    };
    let item = workspace::intake::playlist(
        &request.resolver,
        request.settings,
        request.label,
        &state.handlers,
    )
    .map_err(refused)?;
    let cancel = CancelToken::new();
    let _cancel_when_dropped = CancelOnDrop(cancel.clone());
    let handlers = state.handlers.clone();
    let job = tokio::task::spawn_blocking(move || {
        let content = item.recipe.resolve(&item.id, &handlers, &cancel);
        (item, content)
    });
    let (item, content) = job.await.map_err(|error| {
        tracing::error!("Resolving a playlist failed: {error}");
        failure(
            StatusCode::INTERNAL_SERVER_ERROR,
            &request.resolver,
            "Resolving stopped unexpectedly".to_owned(),
        )
    })?;
    match content {
        Ok(content) => {
            state.store.add_resolved(item, content, true);
            Ok(Json(project(&state.store.snapshot())))
        }
        Err(Interrupt::Failed(error)) => Err(refused(error)),
        // Only a dropped request cancels the resolving, so nobody reads this answer.
        Err(Interrupt::Cancelled) => Err(failure(
            StatusCode::SERVICE_UNAVAILABLE,
            &request.resolver,
            "Resolving was cancelled".to_owned(),
        )),
    }
}

/// Hides or shows an entry or one of its lanes. The target must exist now; the store keeps the reference and ignores
/// it if the content goes away.
async fn set_hidden(
    State(state): State<AppState>,
    Json(request): Json<HiddenRequest>,
) -> Result<Json<WorkspaceState>, (StatusCode, Json<ApiError>)> {
    let snapshot = state.store.snapshot();
    let missing = |location: &str, what: &str| {
        failure(StatusCode::NOT_FOUND, location, format!("No such {what}"))
    };
    let HiddenTarget { entry, lane } = request.target;
    let Some(found) = snapshot.entry(&entry) else {
        return Err(missing(&entry, "entry"));
    };
    let target = match lane {
        None => HiddenRef::Entry(found.id.clone()),
        Some(lane) if found.overlays.iter().any(|overlay| overlay.label == lane) => {
            HiddenRef::Lane(found.id.clone(), lane)
        }
        Some(lane) => return Err(missing(&lane, "lane")),
    };
    state.store.set_hidden(target, request.hidden);
    Ok(Json(project(&state.store.snapshot())))
}

async fn events(
    State(state): State<AppState>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let stream =
        WatchStream::new(state.store.subscribe()).filter_map(|state| {
            match Event::default().json_data(ServerEvent::Workspace(project(&state))) {
                Ok(event) => Some(Ok(event)),
                Err(error) => {
                    tracing::error!("Cannot encode a workspace event: {error}");
                    None
                }
            }
        });
    Sse::new(stream).keep_alive(KeepAlive::default())
}

#[cfg(test)]
mod tests {
    use crate::test_support::{TestApp, json};
    use axum::{
        body::{Body, BodyDataStream},
        http::{Request, StatusCode},
    };
    use serde_json::{Value, json};
    use std::time::Duration;
    use tokio_stream::StreamExt;
    use tower::ServiceExt;

    struct Events(BodyDataStream);

    impl Events {
        async fn connect(test: &TestApp) -> Self {
            let request = Request::builder().uri("/api/events").body(Body::empty());
            let response = test.router.clone().oneshot(request.unwrap()).await.unwrap();
            assert_eq!(response.status(), StatusCode::OK);
            Self(response.into_body().into_data_stream())
        }

        /// The next event's JSON; each event is one chunk, `data: <json>` and a blank line.
        async fn next(&mut self) -> Value {
            let chunk = tokio::time::timeout(Duration::from_secs(10), self.0.next())
                .await
                .unwrap()
                .unwrap()
                .unwrap();
            let text = String::from_utf8(chunk.to_vec()).unwrap();
            json(text.strip_prefix("data: ").unwrap().trim_end().as_bytes())
        }

        /// Reads events until no item is pending, and returns that state.
        async fn until_settled(&mut self) -> Value {
            loop {
                let event = self.next().await;
                if event["items"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .all(|item| item["resolution"]["status"] != "Pending")
                {
                    return event;
                }
            }
        }
    }

    fn post(body: &Value) -> Request<Body> {
        Request::builder()
            .method("POST")
            .uri("/api/workspace/items")
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap()
    }

    fn video(path: &std::path::Path) -> Value {
        json!({"type": "Video", "path": path.display().to_string()})
    }

    #[tokio::test]
    async fn an_added_video_streams_from_pending_to_resolved_and_the_routes_agree_on_its_shape() {
        let test = TestApp::new();
        let file = test.folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let mut events = Events::connect(&test).await;
        let first = events.next().await;
        assert_eq!(
            first,
            json!({"type": "Workspace", "name": null, "items": [], "rows": []}),
            "a new connection starts with the current state, an Untitled empty workspace"
        );

        let (status, body) = test
            .send(post(&json!({"items": [video(&file)], "open": true})))
            .await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(json(&body)["items"][0]["open"], true);

        let settled = events.until_settled().await;
        let [good] = &settled["items"].as_array().unwrap()[..] else {
            panic!("expected one item in {settled}");
        };
        assert_eq!(good["label"], "gate.mp4");
        assert_eq!(good["resolution"]["status"], "Resolved");
        assert_eq!(good["resolution"]["content"]["kind"], "SeekableVideo");
        assert_eq!(good["resolution"]["content"]["display_name"], "gate.mp4");
        let (_, current) = test
            .send(
                Request::builder()
                    .uri("/api/workspace")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await;
        assert_eq!(json(&current)["items"], settled["items"]);
    }

    #[tokio::test]
    async fn a_request_the_intake_refuses_adds_nothing() {
        let test = TestApp::new();
        let file = test.folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();

        let (status, body) = test
            .send(post(
                &json!({"items": [video(&file), {"type": "Video", "path": ""}], "open": false}),
            ))
            .await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert!(!json(&body)["reason"].as_str().unwrap().is_empty());

        let (_, current) = test
            .send(
                Request::builder()
                    .uri("/api/workspace")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await;
        assert_eq!(json(&current)["items"], json!([]));
    }

    fn post_to(uri: &str, body: &Value) -> Request<Body> {
        Request::builder()
            .method("POST")
            .uri(uri)
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap()
    }

    async fn workspace(test: &TestApp) -> Value {
        let request = Request::builder()
            .uri("/api/workspace")
            .body(Body::empty())
            .unwrap();
        json(&test.send(request).await.1)
    }

    #[tokio::test]
    async fn a_playlist_that_fails_to_resolve_names_the_setting_and_adds_nothing() {
        let test = TestApp::new();
        let gone = test.folder.path().join("gone");
        let attempts = [
            (json!({"resolver": "mot_challenge", "settings": {}}), "root"),
            (
                json!({"resolver": "mot_challenge", "settings": {"root": gone}}),
                "gone",
            ),
        ];

        for (body, location) in attempts {
            let (status, answer) = test.send(post_to("/api/workspace/playlists", &body)).await;
            assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{body}");
            let answer = json(&answer);
            assert!(!answer["reason"].as_str().unwrap().is_empty());
            assert!(
                answer["location"].as_str().unwrap().ends_with(location),
                "{answer}"
            );
        }

        let current = workspace(&test).await;
        assert_eq!(current["items"], json!([]));
        assert_eq!(current["rows"], json!([]));
    }

    #[tokio::test]
    async fn a_resolved_playlist_is_added_open_with_its_rows_and_entries_hide_and_show() {
        let test = TestApp::new();
        let root = test.folder.path().join("mot");
        for name in ["SEQ-01", "SEQ-02"] {
            std::fs::create_dir_all(root.join(name).join("gt")).unwrap();
            std::fs::write(root.join(name).join("gt/gt.txt"), "").unwrap();
            std::fs::write(
                root.join(name).join("seqinfo.ini"),
                "[Sequence]\nframeRate=25\nseqLength=10\nimWidth=64\nimHeight=48\n",
            )
            .unwrap();
        }

        let (status, body) = test
            .send(post_to(
                "/api/workspace/playlists",
                &json!({"resolver": "mot_challenge", "settings": {"root": root}}),
            ))
            .await;

        assert_eq!(status, StatusCode::OK);
        let added = json(&body);
        let [item] = &added["items"].as_array().unwrap()[..] else {
            panic!("expected one item in {added}");
        };
        assert_eq!(item["open"], true);
        assert_eq!(item["resolution"]["status"], "Resolved", "no pending phase");
        let entries = &item["resolution"]["content"]["entries"];
        assert_eq!(entries[1]["label"], "SEQ-02");
        assert_eq!(entries[1]["lanes"], json!(["GT"]));
        let rows = added["rows"].as_array().unwrap();
        let kinds: Vec<_> = rows
            .iter()
            .map(|row| row["kind"].as_str().unwrap())
            .collect();
        assert_eq!(kinds, ["item", "entry", "lane", "entry", "lane"]);

        let entry = entries[1]["id"].as_str().unwrap();
        let hide = |target: Value, hidden: bool| {
            post_to(
                "/api/workspace/hidden",
                &json!({"target": target, "hidden": hidden}),
            )
        };
        let (_, hidden) = test
            .send(hide(json!({"entry": entry, "lane": "GT"}), true))
            .await;
        assert_eq!(json(&hidden)["rows"][4]["hidden"], true);
        test.send(hide(json!({"entry": entry}), true)).await;
        assert_eq!(workspace(&test).await["rows"][3]["hidden"], true);
        test.send(hide(json!({"entry": entry}), false)).await;
        assert_eq!(workspace(&test).await["rows"][3]["hidden"], false);

        for target in [
            json!({"entry": "nope"}),
            json!({"entry": entry, "lane": "x"}),
        ] {
            let (status, _) = test.send(hide(target, true)).await;
            assert_eq!(status, StatusCode::NOT_FOUND);
        }
    }
}
