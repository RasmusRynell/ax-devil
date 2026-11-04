//! The items a command line asks the first window to open.

use super::{Command, args};
use ax_devil::session::Launch;
use foundation::{Location, UserError};
use handlers::Handlers;
use workspace::{WorkspaceItem, intake::NewOverlay};
use workspace_files::RecoveryFolder;

/// What `command` asks the first window to start from: the workspace file or items it names, or, for a plain launch,
/// the unsaved work of the last window. The error says what was refused and why.
pub(super) fn launch(
    command: Option<Command>,
    handlers: &Handlers,
    recovery: &RecoveryFolder,
) -> Result<Option<Launch>, String> {
    match command {
        None => Ok(Some(Launch::Restore)),
        Some(Command::Open { file }) => {
            let path = std::path::absolute(&file)
                .map_err(|error| format!("{}: {error}", file.display()))?;
            let workspace = if recovery.contains(&path) {
                None
            } else {
                Some(workspace_files::load(&path).map_err(|error| describe(&error))?)
            };
            Ok(Some(Launch::Open { path, workspace }))
        }
        command => {
            let items = launch_items(command, handlers)?;
            Ok((!items.is_empty()).then_some(Launch::Items(items)))
        }
    }
}

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
            workspace::intake::playlist(&resolver, settings, handlers)
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
