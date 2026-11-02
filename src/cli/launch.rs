//! The items a command line asks the first window to open.

use super::{Command, args};
use foundation::{Location, UserError};
use handlers::Handlers;
use workspace::{WorkspaceItem, intake::NewOverlay};

/// The items `command` asks the first window to open, built by the same intake as the dialogs; none for a plain
/// launch. The error says what was refused and why.
pub(super) fn launch_items(
    command: Option<Command>,
    handlers: &Handlers,
) -> Result<Vec<WorkspaceItem>, String> {
    let item = match command {
        Some(Command::Local {
            video,
            overlay,
            handler_type,
            overlay_option,
        }) => {
            let mut overlays = Vec::with_capacity(overlay.len());
            for (position, path) in overlay.into_iter().enumerate() {
                let handler = handler_type.get(position).cloned();
                let given = overlay_option
                    .iter()
                    .filter(|option| option.overlay == position);
                let options = args::overlay_settings(handlers, &path, handler.as_deref(), given)
                    .map_err(|error| describe(&error))?;
                overlays.push(NewOverlay {
                    path,
                    handler,
                    label: None,
                    options,
                });
            }
            workspace::intake::video(&video, &overlays, handlers)
        }
        Some(Command::Playlist { resolver, settings }) => {
            workspace::intake::playlist(&resolver, settings, None, handlers)
        }
        _ => return Ok(Vec::new()),
    };
    item.map(|item| vec![item])
        .map_err(|error| describe(&error))
}

/// What was refused and why, led by the file or setting it is about.
pub(super) fn describe(error: &UserError) -> String {
    match &error.location {
        Some(Location::Host(path)) => format!("{}: {}", path.as_path().display(), error.reason),
        Some(Location::Field(key)) => format!("{key}: {}", error.reason),
        Some(Location::Item(_)) | None => error.reason.clone(),
    }
}
