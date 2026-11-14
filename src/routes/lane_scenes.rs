//! A bounded read of one lane's samples, for a view that shows them.

use super::{CancelOnDrop, audience, find_entry, not_found, refuse};
use crate::{AppState, Failure, access::Principal};
use api_types::handlers::{LaneScenesQuery, MAX_LANE_SAMPLES};
use axum::{
    extract::{Query, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use scene_store::{Admission, write_lane};

/// The most bytes one answer holds.
const MOST_BYTES: u64 = 8 << 20;

/// At most [`MAX_LANE_SAMPLES`] samples of the lane from sample number `from`, as a lane file: a header line, then a
/// `sample` line each. A lane still being read answers what it has published so far. The answer fails when it would
/// pass [`MOST_BYTES`]; ask for fewer samples.
pub(super) async fn get(
    principal: Principal,
    State(state): State<AppState>,
    Query(query): Query<LaneScenesQuery>,
) -> Result<Response, Failure> {
    let audience = audience(principal);
    let overlay = find_entry(&state, &query.item, &query.entry)
        .and_then(|entry| {
            entry
                .lane(&query.slot)
                .cloned()
                .ok_or_else(|| not_found("lane"))
        })
        .map_err(|(status, error)| refuse(audience, status, &error))?;
    let range = query.from..query.from.saturating_add(query.count.min(MAX_LANE_SAMPLES));
    let cancel = CancelToken::new();
    let _cancel_when_dropped = CancelOnDrop(cancel.clone());
    let read = tokio::task::spawn_blocking(move || {
        let track = state
            .overlays
            .open_track(&overlay, Admission::Compute, &cancel)?;
        let mut bytes = Vec::new();
        write_lane(&*track, Some(range), MOST_BYTES, &mut bytes, &cancel)?;
        Ok(bytes)
    })
    .await
    .unwrap_or_else(|error| {
        tracing::error!("Reading a lane failed: {error}");
        Err(Interrupt::Failed(UserError::new(
            ErrorCode::Internal,
            "The read stopped unexpectedly",
        )))
    });
    match read {
        Ok(bytes) => Ok((
            [
                (header::CONTENT_TYPE, "application/x-ndjson"),
                (header::CACHE_CONTROL, "no-store"),
            ],
            bytes,
        )
            .into_response()),
        Err(Interrupt::Failed(error)) => {
            Err(refuse(audience, StatusCode::UNPROCESSABLE_ENTITY, &error))
        }
        // Only a dropped request cancels the read, so nobody reads this answer.
        Err(Interrupt::Cancelled) => Err(refuse(
            audience,
            StatusCode::SERVICE_UNAVAILABLE,
            &UserError::new(ErrorCode::Internal, "The read was cancelled"),
        )),
    }
}
