use crate::content::{
    Content, ContentId, Entry, OverlayRef, SeekableVideoContent, VideoRef, check_unique_lanes,
    playlist_content,
};
use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, ItemId, UserError};
use handlers::Handlers;
use playlist_resolvers::Settings;
use std::{fs, io, path::Path};

/// A video file to open, with the overlay files drawn on it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VideoRecipe {
    /// The video file, absolute.
    pub video: HostPath,
    /// The overlay files, each shown as its own lane.
    pub overlays: Vec<OverlayRecipe>,
}

/// An overlay file and the decoder that reads it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OverlayRecipe {
    /// The overlay file, absolute.
    pub path: HostPath,
    /// The decoder handler id.
    pub handler: String,
}

/// A playlist resolver and the settings it resolves.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PlaylistRecipe {
    /// The resolver id.
    pub resolver: String,
    /// The settings the resolver's schema declares.
    pub settings: Settings,
}

/// What an item was made from, and how to rebuild its Content.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Recipe {
    /// A video file with overlays.
    Video(VideoRecipe),
    /// A playlist from a resolver.
    Playlist(PlaylistRecipe),
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
    /// A video recipe checks that its path is a readable regular file, and each overlay that its decoder is known and
    /// its file readable; opening the media is the viewer's job, so a file that is not a video resolves here and
    /// fails when the viewer opens it. Each overlay is a lane labelled with its file name, so two overlays with the
    /// same file name fail.
    ///
    /// A playlist recipe asks its resolver, then checks that entry names and the lane names within each entry are
    /// unique, since they are the identity of entries and lanes. Resolving twice gives the same ids.
    pub fn resolve(
        &self,
        item: &ItemId,
        handlers: &Handlers,
        cancel: &CancelToken,
    ) -> Result<Content, Interrupt<UserError>> {
        cancel.check()?;
        match self {
            Recipe::Video(recipe) => {
                resolve_video(recipe, item, handlers).map_err(Interrupt::Failed)
            }
            Recipe::Playlist(recipe) => {
                let resolver = handlers.resolver(&recipe.resolver).ok_or_else(|| {
                    Interrupt::Failed(
                        UserError::new(
                            ErrorCode::UnknownHandler,
                            format!("No playlist resolver \"{}\"", recipe.resolver),
                        )
                        .at(*item),
                    )
                })?;
                resolver
                    .schema()
                    .validate(&recipe.settings)
                    .map_err(Interrupt::Failed)?;
                let resolved = resolver.resolve(&recipe.settings, cancel)?;
                playlist_content(item, resolved)
                    .map(Content::Playlist)
                    .map_err(Interrupt::Failed)
            }
        }
    }
}

fn resolve_video(
    recipe: &VideoRecipe,
    item: &ItemId,
    handlers: &Handlers,
) -> Result<Content, UserError> {
    check_readable_file(recipe.video.as_path())?;
    let mut overlays = Vec::with_capacity(recipe.overlays.len());
    for overlay in &recipe.overlays {
        let path = overlay.path.as_path();
        if handlers.file_decoder(&overlay.handler).is_none() {
            return Err(UserError::new(
                ErrorCode::UnknownHandler,
                format!("No overlay format \"{}\"", overlay.handler),
            )
            .at(overlay.path.clone()));
        }
        check_readable_file(path)?;
        overlays.push(OverlayRef {
            label: file_name(path),
            path: path.to_path_buf(),
            handler: overlay.handler.clone(),
            options: Settings::new(),
        });
    }
    let entry = Entry {
        id: ContentId::of_item(item),
        label: file_name(recipe.video.as_path()),
        video: VideoRef::File(recipe.video.as_path().to_path_buf()),
        overlays,
    };
    check_unique_lanes(item, &entry)?;
    Ok(Content::SeekableVideo(SeekableVideoContent {
        item_id: *item,
        entry,
    }))
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
