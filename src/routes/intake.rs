//! The question an Add form asks before it adds: can the backend host read these files?
//!
//! It answers with what adding would find, so the form can say why before it sends the command. The caller needs the
//! `Files` grant, as adding a video does, so only someone who may add this machine's files learns whether a path on
//! it exists.

use super::handlers::refuse;
use crate::Failure;
use api_types::workspace::{CheckFilesRequest, FileCheck};
use axum::{Json, http::StatusCode};
use foundation::{ErrorCode, UserError};
use std::path::Path;

/// The most files one question may ask about: a video and its overlays, with room to spare.
const MOST_FILES: usize = 64;

/// Each path, with the reason it cannot be read as a file when it cannot. The host's files are read on a blocking
/// thread.
pub(super) async fn check(
    Json(request): Json<CheckFilesRequest>,
) -> Result<Json<Vec<FileCheck>>, Failure> {
    if request.paths.len() > MOST_FILES {
        return Err(refuse(
            StatusCode::BAD_REQUEST,
            &UserError::new(ErrorCode::InvalidPath, "Too many files to check"),
        ));
    }
    let checks = tokio::task::spawn_blocking(move || {
        request
            .paths
            .into_iter()
            .map(|path| FileCheck {
                problem: workspace::check_readable_file(Path::new(&path))
                    .err()
                    .map(|error| error.reason),
                path,
            })
            .collect()
    })
    .await
    .map_err(|error| {
        refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            &UserError::new(ErrorCode::Internal, error.to_string()),
        )
    })?;
    Ok(Json(checks))
}

#[cfg(test)]
mod tests {
    use crate::test_support::{TestApp, json};
    use axum::{
        body::Body,
        http::{Request, StatusCode},
    };
    use serde_json::{Value, json};

    async fn ask(test: &TestApp, paths: &[&str]) -> (StatusCode, Value) {
        let (status, body) = test
            .send(
                Request::builder()
                    .method("POST")
                    .uri("/api/files/check")
                    .header("content-type", "application/json")
                    .body(Body::from(json!({ "paths": paths }).to_string()))
                    .unwrap(),
            )
            .await;
        (status, json(&body))
    }

    #[tokio::test]
    async fn each_path_says_whether_the_host_can_read_it_as_a_file() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("a.mp4");
        std::fs::write(&file, "x").unwrap();
        let gone = folder.path().join("gone.mp4");
        let [file, gone, directory] =
            [&file, &gone, folder.path()].map(|path| path.to_string_lossy());

        let (status, answer) = ask(&TestApp::new(), &[&file, &gone, &directory]).await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            answer,
            json!([
                { "path": file },
                { "path": gone, "problem": "File not found" },
                { "path": directory, "problem": "Not a file" },
            ])
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_file_the_host_may_not_open_is_reported_unreadable() {
        use std::os::unix::fs::PermissionsExt;
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("locked.mp4");
        std::fs::write(&file, "x").unwrap();
        std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o000)).unwrap();
        // A superuser opens it anyway, which leaves nothing to report.
        if std::fs::File::open(&file).is_ok() {
            return;
        }

        let (_, answer) = ask(&TestApp::new(), &[&file.to_string_lossy()]).await;

        assert!(
            answer[0]["problem"]
                .as_str()
                .is_some_and(|reason| reason.starts_with("Cannot read the file"))
        );
    }
}
