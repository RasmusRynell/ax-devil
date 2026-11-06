use crate::content::{
    Content, ContentId, Entry, OverlayRef, SeekableVideoContent, VideoRef, check_unique_lanes,
    playlist_content,
};
use crate::credentials::Environment;
use crate::live::LiveRecipe;
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
    /// A camera's live stream.
    Live(LiveRecipe),
    /// An item a workspace file held that this build cannot read.
    Unreadable(Unreadable),
}

/// An item the file held that this build cannot read; written back exactly as read, never resolves.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Unreadable {
    /// The item as the file had it.
    pub raw: Value,
    /// Why it cannot be read.
    pub reason: String,
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
    /// What the user named the item, or empty for the default name (see [`WorkspaceItem::display_name`]). A default is
    /// derived, never stored.
    pub label: String,
    /// What the item resolves from.
    pub recipe: Recipe,
}

impl WorkspaceItem {
    /// The name the item shows while it has no Content to name: the label, or else the default of its recipe, which
    /// is the resolver id of a playlist, the file name of a video and the host of a live stream.
    pub fn display_name(&self) -> String {
        if !self.label.is_empty() {
            return self.label.clone();
        }
        match &self.recipe.source {
            Source::Live(recipe) => recipe.default_name(),
            Source::Video(video) => file_name(video.video.as_path()),
            Source::Playlist(playlist) => playlist.resolver.clone(),
            Source::Unreadable(_) => "Unreadable item".to_owned(),
        }
    }

    /// Builds the item's Content: [`Recipe::resolve`] with the item's own id and name.
    pub fn resolve(
        &self,
        handlers: &Handlers,
        env: &Environment,
        cancel: &CancelToken,
    ) -> Result<Content, Interrupt<UserError>> {
        self.recipe
            .resolve(&self.id, &self.display_name(), handlers, env, cancel)
    }
}

impl Recipe {
    /// A recipe for `source` with every lane at its defaults.
    pub fn new(source: Source) -> Self {
        Self {
            source,
            lane_matching: BTreeMap::new(),
        }
    }

    /// Builds the Content of the item `item`, named `name` where a failure speaks of it. Reads the file system, so callers keep it off the UI's executor.
    ///
    /// A video recipe checks that its path and every overlay's path are readable regular files, then that each
    /// overlay's decoder is known and its options valid, so a missing file is reported before a missing decoder;
    /// opening the media is the viewer's job, so a file that is not a video resolves here and fails when the viewer
    /// opens it. Each overlay is a lane with its recipe's label, so two overlays with the same label fail.
    ///
    /// Cancellation is polled between files and overlays and once more after the work, so a cancelled attempt
    /// returns [`Interrupt::Cancelled`] and never a Content.
    ///
    /// A playlist recipe asks its resolver for playlists, numbers repeated playlist and entry names, then checks
    /// that the lane names within each entry are unique, since they are the identity of lanes. Resolving twice gives
    /// the same ids. What the resolver or those checks refuse reads `Playlist resolver '<id>' failed: <why>`.
    ///
    /// A live recipe is checked like one just entered, then its `$NAME` references are expanded from `env`.
    /// Nothing is connected to; the viewer does that.
    pub fn resolve(
        &self,
        item: &ItemId,
        name: &str,
        handlers: &Handlers,
        env: &Environment,
        cancel: &CancelToken,
    ) -> Result<Content, Interrupt<UserError>> {
        cancel.check()?;
        let content = self.build(item, name, handlers, env, cancel)?;
        // The attempt may have been replaced while it worked: nothing is published for it.
        cancel.check()?;
        Ok(content)
    }

    fn build(
        &self,
        item: &ItemId,
        name: &str,
        handlers: &Handlers,
        env: &Environment,
        cancel: &CancelToken,
    ) -> Result<Content, Interrupt<UserError>> {
        match &self.source {
            Source::Video(recipe) => resolve_video(recipe, item, handlers, cancel),
            Source::Playlist(recipe) => {
                let resolver = handlers.resolver(&recipe.resolver).ok_or_else(|| {
                    Interrupt::Failed(
                        UserError::new(ErrorCode::UnknownHandler, not_installed(&recipe.resolver))
                            .at(*item),
                    )
                })?;
                let form = &resolver.spec().form;
                let settings =
                    check_settings(form, &recipe.settings, handlers).map_err(Interrupt::Failed)?;
                let failed = |error: UserError| UserError {
                    reason: format!(
                        "Playlist resolver '{}' failed: {}",
                        recipe.resolver, error.reason
                    ),
                    ..error
                };
                let resolved =
                    resolver
                        .resolve(&settings, cancel)
                        .map_err(|error| match error {
                            Interrupt::Failed(error) => Interrupt::Failed(failed(error)),
                            Interrupt::Cancelled => Interrupt::Cancelled,
                        })?;
                if resolved.playlists.is_empty() {
                    return Err(Interrupt::Failed(
                        UserError::new(
                            ErrorCode::PlaylistInvalid,
                            format!("{name} has nothing to open."),
                        )
                        .at(*item),
                    ));
                }
                playlist_content(item, &resolver.spec().name, resolved)
                    .map(Content::Playlist)
                    .map_err(|error| Interrupt::Failed(failed(error)))
            }
            Source::Live(recipe) => recipe
                .resolve(item, handlers, env)
                .map_err(Interrupt::Failed),
            Source::Unreadable(unreadable) => Err(Interrupt::Failed(
                UserError::new(ErrorCode::WorkspaceInvalid, unreadable.reason.clone()).at(*item),
            )),
        }
    }
}

/// Why an item's playlist resolver cannot run: the handlers hold none with this id.
pub(crate) fn not_installed(resolver: &str) -> String {
    format!("Playlist resolver '{resolver}' is not installed.")
}

fn resolve_video(
    recipe: &VideoRecipe,
    item: &ItemId,
    handlers: &Handlers,
    cancel: &CancelToken,
) -> Result<Content, Interrupt<UserError>> {
    let files = std::iter::once(&recipe.video).chain(recipe.overlays.iter().map(|o| &o.path));
    for file in files {
        cancel.check()?;
        check_readable_file(file.as_path()).map_err(Interrupt::Failed)?;
    }
    let mut overlays = Vec::with_capacity(recipe.overlays.len());
    for overlay in &recipe.overlays {
        cancel.check()?;
        let path = overlay.path.as_path();
        let options =
            decoder_options(handlers, &overlay.handler, &overlay.options).map_err(|error| {
                Interrupt::Failed(UserError {
                    location: Some(Location::Host(HostPath::new(path))),
                    ..error
                })
            })?;
        overlays.push(OverlayRef {
            label: overlay.label.clone(),
            slot: overlay.label.clone(),
            path: path.to_path_buf(),
            handler: overlay.handler.clone(),
            options,
        });
    }
    let entry = Entry {
        id: ContentId::of_item(item),
        label: file_name(recipe.video.as_path()),
        video: VideoRef::File(recipe.video.as_path().to_path_buf()),
        overlays,
    };
    check_unique_lanes(item, &entry).map_err(Interrupt::Failed)?;
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
pub fn absolute_paths(form: &Form, settings: &mut Settings, handlers: &Handlers) {
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

/// The last component of `path` as text; empty when there is none.
pub(crate) fn file_name(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Why `path` cannot be read as a file (missing, not a file, or refused by the host), in words for the user.
pub fn check_readable_file(path: &Path) -> Result<(), UserError> {
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

#[cfg(test)]
mod tests {
    use super::*;

    fn resolve(source: Source) -> UserError {
        let recipe = Recipe::new(source);
        match recipe.resolve(
            &ItemId::generate(),
            "item",
            &Handlers::built_in(),
            &Environment::of_process(),
            &CancelToken::new(),
        ) {
            Err(Interrupt::Failed(error)) => error,
            _ => panic!("the recipe should fail to resolve"),
        }
    }

    #[test]
    fn a_loaded_recipe_is_checked_like_one_made_by_intake() {
        let folder = tempfile::tempdir().unwrap();
        let (video, xml) = (folder.path().join("a.mp4"), folder.path().join("a.xml"));
        fs::write(&video, "").unwrap();
        fs::write(&xml, "").unwrap();
        let overlay = |options: &str| OverlayRecipe {
            path: HostPath::new(&xml),
            handler: "ONVIF_XML".into(),
            label: "a.xml".into(),
            options: serde_json::from_str(options).unwrap(),
        };
        let error = resolve(Source::Video(VideoRecipe {
            video: HostPath::new(&video),
            overlays: vec![overlay(r#"{"not_an_option": 1}"#)],
        }));
        assert_eq!(error.code, ErrorCode::OverlayInvalid);
        assert_eq!(error.location, Some(HostPath::new(&xml).into()));
    }

    #[test]
    fn an_unreadable_item_never_resolves() {
        let error = resolve(Source::Unreadable(Unreadable {
            raw: Value::Null,
            reason: "newer".into(),
        }));
        assert_eq!(
            (error.code, error.reason.as_str()),
            (ErrorCode::WorkspaceInvalid, "newer")
        );
    }
}
