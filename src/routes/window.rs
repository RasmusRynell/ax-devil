//! Routes for the saved position and size of each window, which the desktop shell reads before it opens a window.

use crate::{AppState, failure};
use api_types::{ApiError, window::WindowState};
use axum::{
    Json, Router,
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
};
use std::{
    path::PathBuf,
    sync::{Mutex, PoisonError},
};

/// The file the windows are saved in. Saves write the whole file, so they take turns.
pub struct Windows {
    file: PathBuf,
    saving: Mutex<()>,
}

impl Windows {
    /// The windows saved in `file`, which need not exist yet.
    pub fn new(file: PathBuf) -> Self {
        Self {
            file,
            saving: Mutex::new(()),
        }
    }
}

/// The window routes.
pub fn router() -> Router<AppState> {
    Router::new().route("/api/windows/{name}", get(get_window).put(put_window))
}

/// A window name is lower-case words joined by hyphens, so it is safe as a key in the file.
fn named(name: &str) -> Result<(), (StatusCode, Json<ApiError>)> {
    if !name.is_empty() && name.chars().all(|c| c.is_ascii_lowercase() || c == '-') {
        return Ok(());
    }
    Err(failure(
        StatusCode::UNPROCESSABLE_ENTITY,
        "name",
        "a window name is lower-case letters and hyphens".into(),
    ))
}

/// The saved window, or 204 when none is saved or what is saved cannot be read.
async fn get_window(
    State(AppState { windows, .. }): State<AppState>,
    Path(name): Path<String>,
) -> Result<Response, (StatusCode, Json<ApiError>)> {
    named(&name)?;
    let saved = tokio::task::spawn_blocking(move || {
        settings::window::load_windows(&windows.file).remove(&name)
    })
    .await
    .map_err(internal)?;
    Ok(
        match saved.and_then(|value| serde_json::from_value::<WindowState>(value).ok()) {
            Some(window) => Json(window).into_response(),
            None => StatusCode::NO_CONTENT.into_response(),
        },
    )
}

/// Saves the window, keeping the others.
async fn put_window(
    State(AppState { windows, .. }): State<AppState>,
    Path(name): Path<String>,
    Json(window): Json<WindowState>,
) -> Result<StatusCode, (StatusCode, Json<ApiError>)> {
    named(&name)?;
    let saved = tokio::task::spawn_blocking(move || {
        let _saving = windows
            .saving
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let state = serde_json::to_value(window).map_err(|error| error.to_string())?;
        settings::window::save_window(&windows.file, &name, state).map_err(|error| {
            tracing::error!("Cannot save the window: {error}");
            error.to_string()
        })
    })
    .await
    .map_err(internal)?;
    saved
        .map(|()| StatusCode::NO_CONTENT)
        .map_err(|reason| failure(StatusCode::INTERNAL_SERVER_ERROR, "windows", reason))
}

fn internal(error: tokio::task::JoinError) -> (StatusCode, Json<ApiError>) {
    failure(
        StatusCode::INTERNAL_SERVER_ERROR,
        "windows",
        error.to_string(),
    )
}
