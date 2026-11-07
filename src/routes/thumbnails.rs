//! The thumbnail of an entry: its first picture as a JPEG, for the hover cards of the sidebar.

use super::handlers::{Refusal, refuse};
use crate::{AppState, thumbnails::Answer, video_sources::input_of};
use axum::{
    extract::{Query, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
};
use foundation::{ErrorCode, UserError};
use serde::Deserialize;
use sessions::SessionId;
use workspace::ContentId;
use workspace_store::Resolution;

/// The entry to show and the session whose workspace holds it.
#[derive(Deserialize)]
pub(super) struct Params {
    session: String,
    entry: ContentId,
}

fn not_found() -> Refusal {
    refuse(
        StatusCode::NOT_FOUND,
        &UserError::new(ErrorCode::ContentNotFound, "No such entry"),
    )
}

/// Answers with the first picture of the entry, tagged so that a client that holds it is told it is current. A
/// request the client aborts stops the decode when no other request waits for it.
pub(super) async fn get(
    State(state): State<AppState>,
    Query(params): Query<Params>,
    headers: HeaderMap,
) -> Result<Response, Refusal> {
    let session = params
        .session
        .parse::<SessionId>()
        .ok()
        .and_then(|id| state.sessions.subscribe(id))
        .map(|watcher| watcher.borrow().clone())
        .ok_or_else(not_found)?;
    let video = session
        .doc
        .workspace
        .resolutions
        .values()
        .find_map(|resolution| match resolution {
            Resolution::Resolved { content, .. } => content.entry(&params.entry),
            _ => None,
        })
        .map(|entry| input_of(&entry.video))
        .ok_or_else(not_found)?;
    let known = headers
        .get(header::IF_NONE_MATCH)
        .and_then(|value| value.to_str().ok())
        .map(|value| value.trim_start_matches("W/").trim_matches('"').to_owned());
    let answer = state.thumbnails.answer(video, known).await;
    match answer {
        Ok(Answer::Unchanged) => Ok(StatusCode::NOT_MODIFIED.into_response()),
        Ok(Answer::Picture { tag, jpeg }) => {
            // A tag is hexadecimal and a dash, which a header value always holds.
            let etag = HeaderValue::from_str(&format!("\"{tag}\"")).map_err(|error| {
                refuse(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    &UserError::new(ErrorCode::Internal, error.to_string()),
                )
            })?;
            Ok((
                [
                    (header::CONTENT_TYPE, HeaderValue::from_static("image/jpeg")),
                    (header::ETAG, etag),
                    (
                        header::CACHE_CONTROL,
                        HeaderValue::from_static("private, no-cache"),
                    ),
                ],
                jpeg,
            )
                .into_response())
        }
        Err(error) => Err(refuse(StatusCode::UNPROCESSABLE_ENTITY, &error)),
    }
}
