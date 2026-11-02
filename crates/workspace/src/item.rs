use crate::content::{
    Content, ContentId, Entry, OverlayRef, SeekableVideoContent, VideoRef, check_unique_lanes,
    playlist_content,
};
use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, ItemId, Location, UserError};
use handler_api::{FieldKind, Form, Settings, Values};
use handlers::Handlers;
use overlay_sync::MatchPolicy;
use serde_json::Value;
use std::{collections::BTreeMap, fs, io, path::Path};

/// A video file to open, with the overlay files drawn on it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VideoRecipe {
    /// The video file, absolute.
    pub video: HostPath,
    /// The overlay files, each shown as its own lane.
    pub overlays: Vec<OverlayRecipe>,
}

/// An overlay file and the decoder that reads it, shown as a lane of its own: one file may be several lanes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OverlayRecipe {
    /// The overlay file, absolute.
    pub path: HostPath,
    /// The decoder handler id.
    pub handler: String,
    /// The lane's name, unique within the video.
    pub label: String,
    /// Options handed to the decoder, checked against its form when the recipe was made.
    pub options: Settings,
}

/// A playlist resolver and the settings it resolves.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PlaylistRecipe {
    /// The resolver id.
    pub resolver: String,
    /// The settings the resolver's schema declares.
    pub settings: Settings,
}

/// What an item was made from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Source {
    /// A video file with overlays.
    Video(VideoRecipe),
    /// A playlist from a resolver.
    Playlist(PlaylistRecipe),
}

/// What an item was made from, and how to rebuild its Content.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Recipe {
    /// What the Content is resolved from.
    pub source: Source,
    /// How each lane matches its file's records to frames, by the lane's slot: the slot is the same in every entry
    /// of a playlist, where lane names can differ. A lane without an entry is at the decoder's default basis and the
    /// app's defaults.
    pub lane_matching: BTreeMap<String, MatchPolicy>,
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
    /// A recipe for `source` with every lane at its defaults.
    pub fn new(source: Source) -> Self {
        Self {
            source,
            lane_matching: BTreeMap::new(),
        }
    }

    /// Builds the Content of the item `item`. Reads the file system, so callers keep it off the UI's executor.
    ///
    /// A video recipe checks that its path is a readable regular file, and each overlay that its decoder is known and
    /// its file readable; opening the media is the viewer's job, so a file that is not a video resolves here and
    /// fails when the viewer opens it. Each overlay is a lane with its recipe's label, so two overlays with the
    /// same label fail.
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
        match &self.source {
            Source::Video(recipe) => {
                resolve_video(recipe, item, handlers).map_err(Interrupt::Failed)
            }
            Source::Playlist(recipe) => {
                let resolver = handlers.resolver(&recipe.resolver).ok_or_else(|| {
                    Interrupt::Failed(
                        UserError::new(
                            ErrorCode::UnknownHandler,
                            format!("No playlist resolver \"{}\"", recipe.resolver),
                        )
                        .at(*item),
                    )
                })?;
                let form = &resolver.spec().form;
                let settings =
                    check_settings(form, &recipe.settings, handlers).map_err(Interrupt::Failed)?;
                let resolved = resolver.resolve(&settings, cancel)?;
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
        check_decoder_known(handlers, &overlay.handler, path)?;
        check_readable_file(path)?;
        overlays.push(OverlayRef {
            label: overlay.label.clone(),
            slot: overlay.label.clone(),
            path: path.to_path_buf(),
            handler: overlay.handler.clone(),
            options: overlay.options.clone(),
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

/// `settings` checked against `form`, and every decoder id a `Handler` field holds known to `handlers`: a playlist
/// whose settings name an overlay format the app lacks is refused before it reads any folder. The options of a
/// `DecoderOptions` field are checked against the chosen decoder's form and come back with its defaults filled in; an
/// error about them is located at that field and names the option.
pub(crate) fn check_settings(
    form: &Form,
    settings: &Settings,
    handlers: &Handlers,
) -> Result<Values, UserError> {
    let mut values = form.validate(settings)?;
    for field in &form.fields {
        if matches!(field.kind, FieldKind::Handler)
            && let Some(id) = values.text(&field.key)
            && handlers.decoder(id).is_none()
        {
            return Err(UserError::new(
                ErrorCode::UnknownHandler,
                format!("No overlay format \"{id}\""),
            )
            .at(Location::field(&field.key)));
        }
    }
    for field in &form.fields {
        let FieldKind::DecoderOptions { handler_field } = &field.kind else {
            continue;
        };
        let Some(id) = values.text(handler_field) else {
            continue;
        };
        let checked =
            decoder_options(handlers, id, &values.options(&field.key)).map_err(|error| {
                let named = match &error.location {
                    Some(Location::Field(key)) => handlers
                        .decoder(id)
                        .and_then(|decoder| {
                            decoder.spec().options.fields.iter().find(|o| o.key == *key)
                        })
                        .map_or(key.as_str(), |option| option.label.as_str()),
                    _ => "Options",
                };
                UserError::new(
                    ErrorCode::PlaylistInvalid,
                    format!("{named}: {}", error.reason),
                )
                .at(Location::field(&field.key))
            })?;
        values.set_options(&field.key, checked);
    }
    Ok(values)
}

/// `options` checked against the form of the decoder `handler`: the options it will be given, with defaults filled
/// in and file and folder paths made absolute. A missing required option, an unknown decoder or an option the
/// decoder does not declare is refused, naming the key.
pub(crate) fn decoder_options(
    handlers: &Handlers,
    handler: &str,
    options: &Settings,
) -> Result<Settings, UserError> {
    let form = handlers
        .decoder(handler)
        .map(|decoder| &decoder.spec().options)
        .ok_or_else(|| {
            UserError::new(
                ErrorCode::UnknownHandler,
                format!("No overlay format \"{handler}\""),
            )
        })?;
    let values = form.validate(options).map_err(|error| UserError {
        code: ErrorCode::OverlayInvalid,
        ..error
    })?;
    if let Some(key) = values.unknown_keys().first() {
        return Err(UserError::new(
            ErrorCode::OverlayInvalid,
            format!("Not an option of {handler}"),
        )
        .at(Location::field(*key)));
    }
    let mut options = values.declared();
    absolute_paths(form, &mut options, handlers);
    Ok(options)
}

/// Makes the file and folder paths in `settings` absolute, including those in the options of a decoder a
/// `DecoderOptions` field belongs to, so a stored path means the same file whatever the working folder.
pub(crate) fn absolute_paths(form: &Form, settings: &mut Settings, handlers: &Handlers) {
    for field in &form.fields {
        match &field.kind {
            FieldKind::Folder | FieldKind::File { .. } => {
                if let Some(Value::String(path)) = settings.get_mut(&field.key)
                    && let Ok(absolute) = std::path::absolute(&*path)
                {
                    *path = absolute.to_string_lossy().into_owned();
                }
            }
            FieldKind::DecoderOptions { handler_field } => {
                let chosen = settings
                    .get(handler_field)
                    .and_then(Value::as_str)
                    .and_then(|id| handlers.decoder(id));
                if let (Some(decoder), Some(Value::Object(options))) =
                    (chosen, settings.get_mut(&field.key))
                {
                    absolute_paths(&decoder.spec().options, options, handlers);
                }
            }
            _ => {}
        }
    }
}

/// Fails, about `path`, when `handlers` has no decoder with the id `handler`.
fn check_decoder_known(handlers: &Handlers, handler: &str, path: &Path) -> Result<(), UserError> {
    match handlers.decoder(handler) {
        Some(_) => Ok(()),
        None => Err(UserError::new(
            ErrorCode::UnknownHandler,
            format!("No overlay format \"{handler}\""),
        )
        .at(HostPath::new(path))),
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
