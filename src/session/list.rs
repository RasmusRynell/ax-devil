//! `GET /api/sessions`: the sessions that are shared.

use crate::AppState;
use api_types::SessionSummary;
use axum::{Json, extract::State};

/// Lists the shared sessions for a caller with the Sessions grant (the route table checks it).
pub(crate) async fn list(State(state): State<AppState>) -> Json<Vec<SessionSummary>> {
    Json(
        state
            .sessions
            .list()
            .into_iter()
            .map(|listing| SessionSummary {
                link: state.hosting.join_url(&listing.link),
                id: listing.id.to_string(),
                owner_present: listing.owner_present,
                participants: u32::try_from(listing.seats).unwrap_or(u32::MAX),
            })
            .collect(),
    )
}
