//! The one place that turns what the user chose into workspace items. Dialogs, the command line and drops all
//! come through here, so they accept and refuse the same input.

use crate::item::{
    OverlayRecipe, PlaylistRecipe, Recipe, Source, VideoRecipe, WorkspaceItem, absolute_paths,
    check_settings, decoder_options, file_name, not_installed,
};
use foundation::{ErrorCode, HostPath, ItemId, UserError};
use handler_api::Settings;
use handlers::Handlers;
use std::path::{Path, PathBuf};

mod live_stream;

pub use live_stream::{
    DataHubDefaults, LiveDefaults, LiveMode, MqttDefaults, NewDataHub, NewLive, NewLiveOverlay,
    NewMqtt, live,
};

/// An overlay file the user chose to add to a video.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NewOverlay {
    /// The overlay file.
    pub path: PathBuf,
    /// The decoder handler id; `None` picks the only decoder for the file's extension.
    pub handler: Option<String>,
    /// The lane's name; `None` names it after the file, numbered ` (2)`, ` (3)` when an earlier overlay of the same
    /// video is the same file, so one file can be shown under several catalogs, filters or themes.
    pub label: Option<String>,
    /// Options handed to the decoder, checked against its form when the lane opens.
    pub options: Settings,
}

/// Builds the item for the video file at `path`, with `overlays` drawn on it. The paths are made absolute, so the
/// item means the same files whatever the working folder; whether they exist is for resolving to say.
///
/// An overlay without a handler gets the only decoder for its extension; the error names the candidates when
/// several read it, and the file when none does. A named handler must be one the app knows. Every overlay is a lane
/// with a name of its own: two lanes may read one file, but not carry one name.
pub fn video(
    path: &Path,
    overlays: &[NewOverlay],
    handlers: &Handlers,
) -> Result<WorkspaceItem, UserError> {
    let invalid =
        |reason: &str| UserError::new(ErrorCode::InvalidPath, reason).at(HostPath::new(path));
    let absolute =
        std::path::absolute(path).map_err(|_| invalid("Enter the path of a video file"))?;
    let label = file_name(&absolute);
    if label.is_empty() {
        return Err(invalid("The path does not name a file"));
    }
    let mut recipes: Vec<OverlayRecipe> = Vec::with_capacity(overlays.len());
    for overlay in overlays {
        let recipe = overlay_recipe(overlay, &recipes, handlers)?;
        recipes.push(recipe);
    }
    let overlays = recipes;
    Ok(WorkspaceItem {
        id: ItemId::generate(),
        label,
        recipe: Recipe::new(Source::Video(VideoRecipe {
            video: HostPath::new(absolute),
            overlays,
        })),
    })
}

fn overlay_recipe(
    overlay: &NewOverlay,
    earlier: &[OverlayRecipe],
    handlers: &Handlers,
) -> Result<OverlayRecipe, UserError> {
    let refuse =
        |code, reason: String| UserError::new(code, reason).at(HostPath::new(&overlay.path));
    let path = std::path::absolute(&overlay.path)
        .ok()
        .filter(|path| !file_name(path).is_empty())
        .ok_or_else(|| {
            refuse(
                ErrorCode::InvalidPath,
                "The path does not name a file".into(),
            )
        })?;
    let handler = match &overlay.handler {
        Some(handler) if handlers.decoder(handler).is_some() => handler.clone(),
        Some(handler) => {
            return Err(refuse(
                ErrorCode::UnknownHandler,
                format!("No overlay format \"{handler}\""),
            ));
        }
        None => {
            let candidates: Vec<&str> = handlers
                .decoders_for(&path)
                .iter()
                .map(|decoder| decoder.spec().id.as_str())
                .collect();
            match candidates[..] {
                [only] => only.to_owned(),
                [] => {
                    return Err(refuse(
                        ErrorCode::UnknownHandler,
                        "No overlay format reads this kind of file".into(),
                    ));
                }
                _ => {
                    return Err(refuse(
                        ErrorCode::UnknownHandler,
                        format!(
                            "Several overlay formats read this file; choose one of {}",
                            candidates.join(", ")
                        ),
                    ));
                }
            }
        }
    };
    let options = decoder_options(handlers, &handler, &overlay.options)?;
    let path = HostPath::new(path);
    let label = match overlay.label.as_deref().map(str::trim) {
        Some(label) if !label.is_empty() => label.to_owned(),
        _ => {
            let name = file_name(path.as_path());
            match earlier.iter().filter(|other| other.path == path).count() {
                0 => name,
                repeats => format!("{name} ({})", repeats + 1),
            }
        }
    };
    Ok(OverlayRecipe {
        path,
        handler,
        label,
        options,
    })
}

/// Builds the item for a playlist of `resolver` with `settings`, unlabelled: its name is the default, the resolver
/// id. The settings are checked against the resolver's schema, defaults filled in, and the paths in them (in a decoder's
/// options too) made absolute; keys the schema does not declare are kept, since the resolver only ever sees the
/// declared ones. Reading the folders is for resolving to do.
pub fn playlist(
    resolver: &str,
    settings: Settings,
    handlers: &Handlers,
) -> Result<WorkspaceItem, UserError> {
    let Some(found) = handlers.resolver(resolver) else {
        return Err(UserError::new(
            ErrorCode::UnknownHandler,
            not_installed(resolver),
        ));
    };
    let form = &found.spec().form;
    let mut settings = check_settings(form, &settings, handlers)?.all();
    absolute_paths(form, &mut settings, handlers);
    Ok(WorkspaceItem {
        id: ItemId::generate(),
        label: String::new(),
        recipe: Recipe::new(Source::Playlist(PlaylistRecipe {
            resolver: resolver.to_owned(),
            settings,
        })),
    })
}

/// `item` labelled `label`: the one rule for naming an item the user named. A label that is empty once trimmed, or
/// that is the item's default name, clears the label so the default shows.
pub fn with_label(item: WorkspaceItem, label: Option<&str>) -> WorkspaceItem {
    let unnamed = WorkspaceItem {
        label: String::new(),
        ..item
    };
    match label.map(str::trim) {
        Some(label) if label != unnamed.display_name() => WorkspaceItem {
            label: label.to_owned(),
            ..unnamed
        },
        _ => unnamed,
    }
}

#[cfg(test)]
mod live_tests;
#[cfg(test)]
mod tests;
