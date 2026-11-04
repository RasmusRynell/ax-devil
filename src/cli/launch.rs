//! The items a command line asks the first window to open.

use super::{Command, args};
use ax_devil::session::Launch;
use foundation::{Location, UserError};
use handlers::Handlers;
use std::path::Path;
use workspace::{
    WorkspaceItem,
    intake::{LiveDefaults, NewOverlay},
};
use workspace_files::{Claim, RecoveryFolder};

/// What `command` asks the first window to start from: the workspace file or items it names, or, for a plain launch,
/// the unsaved work of the last window. `defaults` is read only for `live`. The error says what was refused and why.
pub(super) fn launch(
    command: Option<Command>,
    handlers: &Handlers,
    recovery: &RecoveryFolder,
    defaults: impl FnOnce() -> Result<LiveDefaults, String>,
) -> Result<Option<Launch>, String> {
    match command {
        None => Ok(Some(Launch::Restore)),
        Some(Command::Open { file }) => {
            let path = std::path::absolute(&file)
                .map_err(|error| format!("{}: {error}", file.display()))?;
            let workspace = if recovery.contains(&path) {
                check_recovery_copy(recovery, &path)?;
                None
            } else {
                Some(workspace_files::load(&path).map_err(|error| describe(&error))?)
            };
            Ok(Some(Launch::Open { path, workspace }))
        }
        command => {
            let items = launch_items(command, handlers, defaults)?;
            Ok((!items.is_empty()).then_some(Launch::Items(items)))
        }
    }
}

/// Refuses a recovery copy the window could not take over: one that is missing, held by another window, or not a
/// readable workspace. The session claims and reads it again once the app is up; the lock is not kept in between.
fn check_recovery_copy(recovery: &RecoveryFolder, path: &Path) -> Result<(), String> {
    match recovery.claim(path) {
        Ok(copy) => copy.read().map(drop).map_err(|error| describe(&error)),
        Err(Claim::InUse) => Err(format!("{}: open in another window", path.display())),
        Err(Claim::Failed(error)) => Err(describe(&error)),
    }
}

/// The items `command` asks the first window to open, built by the same intake as the dialogs; none for a plain
/// launch. `defaults` is read only for `live`. The error says what was refused and why.
pub(super) fn launch_items(
    command: Option<Command>,
    handlers: &Handlers,
    defaults: impl FnOnce() -> Result<LiveDefaults, String>,
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
                if handler.is_none() {
                    choose_handler(handlers, &path)?;
                }
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
        Some(Command::Live(args)) => {
            let defaults = defaults()?;
            workspace::intake::live(args.into_new_live(&defaults), &defaults, handlers)
        }
        _ => return Ok(Vec::new()),
    };
    item.map(|item| vec![item])
        .map_err(|error| describe(&error))
}

/// Whether the overlay file `path` has exactly one decoder to read it. Otherwise the error says how to correct the
/// command line: the handlers to list, or the ones to choose between with `--handler-type`.
fn choose_handler(handlers: &Handlers, path: &Path) -> Result<(), String> {
    let name = path
        .file_name()
        .unwrap_or(path.as_os_str())
        .to_string_lossy();
    match handlers.decoders_for(path)[..] {
        [_] => Ok(()),
        [] => Err(format!(
            "No overlay handler reads {name}. List them with: ax-devil list-handlers"
        )),
        ref candidates => {
            let ids: Vec<_> = candidates
                .iter()
                .map(|decoder| decoder.spec().id.as_str())
                .collect();
            Err(format!(
                "Several overlay handlers may read {name}: {}. Choose with --handler-type, once per --overlay, in the same order.",
                ids.join(", ")
            ))
        }
    }
}

/// What was refused and why, led by the file or setting it is about.
pub(super) fn describe(error: &UserError) -> String {
    match &error.location {
        Some(Location::Host(path)) => format!("{}: {}", path.as_path().display(), error.reason),
        Some(Location::Field(key)) => format!("{key}: {}", error.reason),
        Some(Location::Item(_)) | None => error.reason.clone(),
    }
}
