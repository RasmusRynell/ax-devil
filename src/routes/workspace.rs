//! Routes for the workspace: its state, adding items through the intake, and the stream of state changes.
//!
//! Changes reach the frontend as server-sent events, not a WebSocket: the traffic is one-way, small and needs only
//! reconnection, which `EventSource` does by itself. Every event carries the whole state, so a client that connects
//! or reconnects needs nothing else.

use crate::{AppState, failure};
use api_types::{
    ApiError,
    workspace::{
        AddItemsRequest, ContentInfo, ItemState, NewItem, ResolutionState, ServerEvent,
        WorkspaceState,
    },
};
use axum::{
    Json, Router,
    extract::State,
    http::StatusCode,
    response::sse::{Event, KeepAlive, Sse},
    routing::{get, post},
};
use std::{convert::Infallible, path::Path};
use tokio_stream::{Stream, StreamExt, wrappers::WatchStream};
use workspace::Content;
use workspace_store::{Resolution, StoreState};

/// The workspace routes.
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/workspace", get(get_workspace))
        .route("/api/workspace/items", post(add_items))
        .route("/api/events", get(events))
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
                                id: video.id.to_string(),
                                display_name: video.display_name.clone(),
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
    let items = request
        .items
        .iter()
        .map(|new| match new {
            NewItem::Video { path } => workspace::intake::video(Path::new(path)),
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

    struct Events(BodyDataStream);

    impl Events {
        async fn connect(test: &TestApp) -> Self {
            let request = Request::builder().uri("/api/events").body(Body::empty());
            let response = test.open(request.unwrap()).await;
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
            json!({"type": "Workspace", "name": null, "items": []}),
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
}
