//! Listing a folder of the backend's machine, so an Owner who is not at it can choose a file or folder there.
//!
//! It lists names and kinds only. An Owner can already open any path on the server by typing it, so this needs no
//! more than the Owner role.

use super::handlers::refuse;
use crate::Failure;
use api_types::workspace::{FolderEntry, FolderListing, FolderPlace, PathExists};
use axum::{Json, extract::Query, http::StatusCode};
use foundation::{ErrorCode, UserError};
use serde::Deserialize;
use std::{
    fs, io,
    path::{Path, PathBuf},
};

/// The folder to list.
#[derive(Deserialize)]
pub(super) struct Params {
    /// An absolute path; the starting places only when absent.
    path: Option<String>,
}

/// The folder asked for, or only the places to start from when none was. The host's folder is read on a blocking
/// thread.
pub(super) async fn list(Query(params): Query<Params>) -> Result<Json<FolderListing>, Failure> {
    tokio::task::spawn_blocking(move || listing(params.path.as_deref()))
        .await
        .map_err(|error| {
            refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                &UserError::new(ErrorCode::Internal, error.to_string()),
            )
        })?
        .map(Json)
}

/// Whether anything is at the absolute path asked, as the filesystem names it: hidden entries count, and on a
/// case-insensitive filesystem so does another spelling. A link that leads nowhere is still there.
pub(super) async fn exists(Query(params): Query<Params>) -> Result<Json<PathExists>, Failure> {
    let path = PathBuf::from(params.path.unwrap_or_default());
    if !path.is_absolute() {
        return Err(refuse(
            StatusCode::BAD_REQUEST,
            &UserError::new(ErrorCode::InvalidPath, "Not an absolute path"),
        ));
    }
    tokio::task::spawn_blocking(move || fs::symlink_metadata(path).is_ok())
        .await
        .map(|exists| Json(PathExists { exists }))
        .map_err(|error| {
            refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                &UserError::new(ErrorCode::Internal, error.to_string()),
            )
        })
}

fn listing(path: Option<&str>) -> Result<FolderListing, Failure> {
    let Some(path) = path else {
        return Ok(FolderListing {
            path: None,
            parent: None,
            entries: Vec::new(),
            places: places(),
        });
    };
    let path = Path::new(path);
    if !path.is_absolute() {
        return Err(refuse(
            StatusCode::BAD_REQUEST,
            &UserError::new(ErrorCode::InvalidPath, "Not an absolute path"),
        ));
    }
    let folder = fs::canonicalize(path)
        .map(plain)
        .map_err(|error| problem(&error))?;
    let read = fs::read_dir(&folder).map_err(|error| problem(&error))?;
    let mut entries: Vec<FolderEntry> = read
        .filter_map(Result::ok)
        .filter(|entry| !hidden(entry))
        .filter_map(|entry| {
            // Following a link, so a link to a folder is a folder; one that leads nowhere is skipped.
            let folder = fs::metadata(entry.path()).ok()?.is_dir();
            Some(FolderEntry {
                name: entry.file_name().to_string_lossy().into_owned(),
                folder,
            })
        })
        .collect();
    entries.sort_by_cached_key(|entry| (!entry.folder, entry.name.to_lowercase()));
    Ok(FolderListing {
        parent: folder.parent().map(text),
        path: Some(text(&folder)),
        entries,
        places: Vec::new(),
    })
}

fn problem(error: &io::Error) -> Failure {
    let (status, code, reason) = match error.kind() {
        io::ErrorKind::NotFound => (
            StatusCode::NOT_FOUND,
            ErrorCode::FileNotFound,
            "Folder not found".to_owned(),
        ),
        io::ErrorKind::PermissionDenied => (
            StatusCode::FORBIDDEN,
            ErrorCode::FileUnreadable,
            "Cannot read the folder".to_owned(),
        ),
        _ => (
            StatusCode::BAD_REQUEST,
            ErrorCode::InvalidPath,
            "Not a folder".to_owned(),
        ),
    };
    refuse(status, &UserError::new(code, reason))
}

fn text(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

/// The path without the `\\?\` prefix Windows canonical paths carry.
fn plain(path: PathBuf) -> PathBuf {
    match path.to_str().and_then(|text| text.strip_prefix(r"\\?\")) {
        Some(rest) if rest.as_bytes().get(1) == Some(&b':') => PathBuf::from(rest),
        _ => path,
    }
}

#[cfg(windows)]
fn hidden(entry: &fs::DirEntry) -> bool {
    use std::os::windows::fs::MetadataExt;
    const HIDDEN: u32 = 0x2;
    entry.file_name().to_string_lossy().starts_with('.')
        || entry
            .metadata()
            .is_ok_and(|meta| meta.file_attributes() & HIDDEN != 0)
}

#[cfg(not(windows))]
fn hidden(entry: &fs::DirEntry) -> bool {
    entry.file_name().to_string_lossy().starts_with('.')
}

/// The home folder, then the filesystem roots.
fn places() -> Vec<FolderPlace> {
    let home = ["HOME", "USERPROFILE"]
        .into_iter()
        .find_map(|name| {
            std::env::var_os(name)
                .map(PathBuf::from)
                .filter(|path| path.is_absolute() && path.is_dir())
        })
        .map(|path| FolderPlace {
            name: "Home".to_owned(),
            path: text(&path),
        });
    home.into_iter()
        .chain(roots().into_iter().map(|root| FolderPlace {
            name: root.clone(),
            path: root,
        }))
        .collect()
}

#[cfg(windows)]
fn roots() -> Vec<String> {
    ('A'..='Z')
        .map(|letter| format!(r"{letter}:\"))
        .filter(|root| Path::new(root).exists())
        .collect()
}

#[cfg(not(windows))]
fn roots() -> Vec<String> {
    vec!["/".to_owned()]
}

#[cfg(test)]
mod tests {
    use crate::test_support::{TestApp, json};
    use axum::{
        body::Body,
        http::{Request, StatusCode},
    };
    use serde_json::{Value, json};
    use std::path::Path;

    async fn ask(test: &TestApp, path: Option<&Path>) -> (StatusCode, Value) {
        let uri = match path {
            Some(path) => format!(
                "/api/files/folder?path={}",
                path.to_string_lossy().replace('/', "%2F")
            ),
            None => "/api/files/folder".to_owned(),
        };
        let (status, body) = test
            .send(Request::builder().uri(uri).body(Body::empty()).unwrap())
            .await;
        (status, json(&body))
    }

    #[tokio::test]
    async fn a_folder_lists_folders_first_and_leaves_hidden_entries_out() {
        let folder = tempfile::tempdir().unwrap();
        for file in ["b.txt", "A.txt", ".secret"] {
            std::fs::write(folder.path().join(file), "x").unwrap();
        }
        for subfolder in ["zeta", "Alpha", ".git"] {
            std::fs::create_dir(folder.path().join(subfolder)).unwrap();
        }

        let (status, answer) = ask(&TestApp::new(), Some(folder.path())).await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            answer["entries"],
            json!([
                { "name": "Alpha", "folder": true },
                { "name": "zeta", "folder": true },
                { "name": "A.txt", "folder": false },
                { "name": "b.txt", "folder": false },
            ])
        );
        let canonical = std::fs::canonicalize(folder.path()).unwrap();
        assert_eq!(answer["path"], canonical.to_string_lossy().as_ref());
        assert_eq!(
            answer["parent"],
            canonical.parent().unwrap().to_string_lossy().as_ref()
        );
        // Drives are probed only when the places are asked for.
        assert_eq!(answer["places"], json!([]));
    }

    #[tokio::test]
    async fn exists_counts_hidden_files_and_not_missing_ones() {
        let folder = tempfile::tempdir().unwrap();
        std::fs::write(folder.path().join(".hidden.ax-devil.workspace"), "x").unwrap();
        let test = TestApp::new();
        let mut answers = Vec::new();
        for name in [".hidden.ax-devil.workspace", "gone.ax-devil.workspace"] {
            let path = folder.path().join(name);
            let uri = format!(
                "/api/files/exists?path={}",
                path.to_string_lossy().replace('/', "%2F")
            );
            let (status, body) = test
                .send(Request::builder().uri(uri).body(Body::empty()).unwrap())
                .await;
            assert_eq!(status, StatusCode::OK);
            answers.push(json(&body)["exists"].clone());
        }
        assert_eq!(answers, [json!(true), json!(false)]);
    }

    #[tokio::test]
    async fn no_path_answers_the_places_to_start() {
        let (status, answer) = ask(&TestApp::new(), None).await;

        assert_eq!(status, StatusCode::OK);
        assert!(answer.get("path").is_none());
        assert!(
            answer["places"]
                .as_array()
                .is_some_and(|places| !places.is_empty())
        );
    }

    #[tokio::test]
    async fn a_path_that_is_not_a_folder_is_a_problem() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("a.txt");
        std::fs::write(&file, "x").unwrap();

        let (missing, _) = ask(&TestApp::new(), Some(&folder.path().join("gone"))).await;
        let (not_folder, _) = ask(&TestApp::new(), Some(&file)).await;

        assert_eq!(missing, StatusCode::NOT_FOUND);
        assert_eq!(not_folder, StatusCode::BAD_REQUEST);
    }
}
