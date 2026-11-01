use crate::content::{Content, ContentId, FileVideoSource, SeekableVideoContent};
use foundation::{ErrorCode, HostPath, ItemId, UserError};
use std::{fs, io, path::Path};

/// A video file to open.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VideoRecipe {
    /// The video file, absolute.
    pub video: HostPath,
}

/// What an item was made from, and how to rebuild its Content. Overlay files join the video recipe later.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Recipe {
    /// A video file.
    Video(VideoRecipe),
}

/// One thing the user added to the workspace.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorkspaceItem {
    /// Names the item.
    pub id: ItemId,
    /// The name the item's row shows.
    pub label: String,
    /// What the item resolves from.
    pub recipe: Recipe,
}

impl Recipe {
    /// Builds the Content of the item `item`. Reads the file system, so callers keep it off the UI's executor.
    ///
    /// A video recipe only checks that its path is a readable regular file: opening the media is the viewer's job,
    /// so a file that is not a video resolves here and fails when the viewer opens it.
    pub fn resolve(&self, item: &ItemId) -> Result<Content, UserError> {
        match self {
            Recipe::Video(recipe) => {
                check_readable_file(recipe.video.as_path())?;
                Ok(Content::SeekableVideo(SeekableVideoContent {
                    id: ContentId::of_item(item),
                    item_id: *item,
                    display_name: file_name(recipe.video.as_path()),
                    source: FileVideoSource {
                        path: recipe.video.clone(),
                    },
                }))
            }
        }
    }
}

/// The last component of `path` as text; empty when there is none.
pub(crate) fn file_name(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn check_readable_file(path: &Path) -> Result<(), UserError> {
    let fail = |code, reason: String| UserError::new(code, reason).at(HostPath::new(path));
    let unreadable = |error: io::Error| match error.kind() {
        io::ErrorKind::NotFound => fail(ErrorCode::FileNotFound, "File not found".into()),
        _ => fail(
            ErrorCode::FileUnreadable,
            format!("Cannot read the file: {error}"),
        ),
    };
    if !fs::metadata(path).map_err(unreadable)?.is_file() {
        return Err(fail(ErrorCode::FileUnreadable, "Not a file".into()));
    }
    fs::File::open(path).map_err(unreadable)?;
    Ok(())
}
