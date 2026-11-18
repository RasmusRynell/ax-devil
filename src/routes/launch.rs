//! A launch another process built, for the window the shell opens next: `ax-devil local --video ...` run while the
//! app is already open.

use crate::{AppState, Failure, failure, session::HandOff};
use axum::{Json, extract::State, http::StatusCode};

/// Queues the launch for the next Local session to take.
pub(super) async fn post(
    State(AppState { launch, .. }): State<AppState>,
    Json(handed): Json<HandOff>,
) -> Result<StatusCode, Failure> {
    let handed = handed
        .into_launch()
        .map_err(|reason| failure(StatusCode::UNPROCESSABLE_ENTITY, "launch", reason))?;
    launch.push(handed);
    Ok(StatusCode::NO_CONTENT)
}
