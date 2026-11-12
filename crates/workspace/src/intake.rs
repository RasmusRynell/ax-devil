//! The one place that turns what the user chose into workspace items. Dialogs, the command line and drops all
//! come through here, so they accept and refuse the same input.

use crate::credentials::Environment;
use crate::item::{
    OverlayRecipe, PlaylistRecipe, Recipe, Source, VideoRecipe, WorkspaceItem, absolute_paths,
    check_settings, decoder_options, file_name, not_installed,
};
use foundation::{ErrorCode, HostPath, ItemId, UserError};
use handler_api::Settings;
use handlers::Handlers;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

mod live_defaults;
mod live_stream;

pub use live_defaults::{DataHubDefaults, LiveDefaults, LiveMode, MqttDefaults};
pub use live_stream::{LoginUse, NewDataHub, NewLive, NewLiveOverlay, NewMqtt, live};

/// An overlay file the user chose to add to a video.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NewOverlay {
    /// The overlay file.
    pub path: PathBuf,
    /// The decoder handler id; `None` picks the only decoder for the file's extension.
    pub handler: Option<String>,
    /// The lane's name; `None` names it after the file's stem, numbered ` (2)`, ` (3)` when an earlier overlay of the
    /// same video has that name, whether it is the same file (shown under several catalogs, filters or themes) or
    /// another file of the same name in another folder.
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
    // Every name the caller chose is reserved first, so a name made from a file stem never takes a later one.
    let mut labels = LaneLabels::default();
    labels
        .taken
        .extend(overlays.iter().filter_map(chosen_label).map(str::to_owned));
    for overlay in overlays {
        let recipe = overlay_recipe(overlay, &mut labels, handlers)?;
        recipes.push(recipe);
    }
    let overlays = recipes;
    Ok(WorkspaceItem {
        id: ItemId::generate(),
        label: String::new(),
        recipe: Recipe::new(Source::Video(VideoRecipe {
            video: HostPath::new(absolute),
            overlays,
        })),
    })
}

fn overlay_recipe(
    overlay: &NewOverlay,
    labels: &mut LaneLabels,
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
    let label = match chosen_label(overlay) {
        Some(label) => label.to_owned(),
        None => labels.unused(&file_stem(path.as_path())),
    };
    Ok(OverlayRecipe {
        path,
        handler,
        label,
        options,
    })
}

/// The lane name the caller gave `overlay`, if it gave a non-blank one.
fn chosen_label(overlay: &NewOverlay) -> Option<&str> {
    overlay
        .label
        .as_deref()
        .map(str::trim)
        .filter(|label| !label.is_empty())
}

/// The lane labels a video has so far, so numbering a name costs the same however many lanes came before.
#[derive(Default)]
struct LaneLabels {
    taken: HashSet<String>,
    /// The highest number tried for a stem: every `stem (n)` below it is taken, so numbering resumes there.
    tried: HashMap<String, usize>,
}

impl LaneLabels {
    /// Records `label` as taken and returns it.
    fn take(&mut self, label: String) -> String {
        self.taken.insert(label.clone());
        label
    }

    /// `stem`, or `stem (2)`, `stem (3)` ..., whichever is the first not taken yet.
    fn unused(&mut self, stem: &str) -> String {
        if !self.taken.contains(stem) {
            return self.take(stem.to_owned());
        }
        let tried = self.tried.entry(stem.to_owned()).or_insert(1);
        loop {
            *tried += 1;
            let label = format!("{stem} ({tried})");
            if !self.taken.contains(&label) {
                self.taken.insert(label.clone());
                return label;
            }
        }
    }
}

/// The file name of `path` without its last extension: `gt` for `gt.txt`.
fn file_stem(path: &Path) -> String {
    path.file_stem()
        .map(|stem| stem.to_string_lossy().into_owned())
        .unwrap_or_default()
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

/// `item` labelled `label`: the one rule for naming an item the user named (WSC-003). A label that is empty once
/// trimmed, or exactly the item's current default name, clears the label so the default shows. The default is read
/// in `env`, the environment of whoever names the item: a live stream on a `$NAME` host has the variable's value for
/// its default (the host as typed under a literal environment), or `Live stream` while that is unset or no plain host.
/// Only this clears a label; everywhere else an empty label is the one sign of an unnamed item.
pub fn with_label(item: WorkspaceItem, label: Option<&str>, env: &Environment) -> WorkspaceItem {
    let default = WorkspaceItem {
        label: String::new(),
        ..item.clone()
    }
    .display_name_in(env);
    with_label_as(item, label, &default)
}

/// [`with_label`] against the default name `default` the namer is allowed to know: what its own view of the unnamed
/// item is called, so that clearing a label tells it nothing it could not read.
pub fn with_label_as(item: WorkspaceItem, label: Option<&str>, default: &str) -> WorkspaceItem {
    let unnamed = WorkspaceItem {
        label: String::new(),
        ..item
    };
    match label.map(str::trim) {
        Some(label) if label != default => WorkspaceItem {
            label: label.to_owned(),
            ..unnamed
        },
        _ => unnamed,
    }
}

#[cfg(test)]
mod live_tests;
#[cfg(test)]
mod resolve_tests;
#[cfg(test)]
mod tests;
