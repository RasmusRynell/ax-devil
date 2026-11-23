//! The items a command line asks the first window to open.

use super::{Command, args};
use ax_devil::session::Launch;
use foundation::{Location, UserError};
use handlers::Handlers;
use std::path::Path;
use workspace::{
    Connection, Environment, HostReading, LiveOverlay, LiveRecipe, Secret, Source, WorkspaceItem,
    intake::{LiveDefaults, NewOverlay},
    preview,
};
use workspace_files::{Claim, RecoveryFolder};

/// What `command` asks the first window to start from: the workspace file or items it names, or, for a plain launch,
/// the unsaved work of the last window. `defaults` is read only for `live`, and `env` only to check that its hosts are
/// not references to nothing. The error says what was refused and why.
pub(super) fn launch(
    command: Option<Command>,
    handlers: &Handlers,
    recovery: &RecoveryFolder,
    env: &Environment,
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
            for item in &items {
                if let Source::Live(recipe) = &item.recipe.source {
                    check_hosts(recipe, env)?;
                }
            }
            Ok((!items.is_empty()).then_some(Launch::Items(items)))
        }
    }
}

/// Refuses a live stream whose camera or MQTT broker host is a `$NAME` reference that expands to nothing, or to
/// something that is no plain host name, as resolving would once the app is up. The item keeps the reference as
/// written; the expansion is only looked at here, and the messages never echo what it was.
fn check_hosts(recipe: &LiveRecipe, env: &Environment) -> Result<(), String> {
    let none = Secret::default();
    let host = |host| Connection {
        host,
        user: "",
        password: &none,
    };
    let broker = match &recipe.overlay {
        LiveOverlay::Mqtt { broker_host, .. } => Some(host(broker_host)),
        _ => None,
    };
    let shown = preview(env, host(&recipe.host), broker);
    let refusal = |reading: Option<HostReading>, what: &str, reference: &str, fix: &str| {
        let problem = match reading {
            Some(HostReading::Unset) => "is empty or unset",
            Some(HostReading::Rejected) => "is set but is not a plain host name",
            Some(HostReading::Host(_)) | None => return Ok(()),
        };
        Err(format!("No {what}: {reference} {problem}. {fix}"))
    };
    refusal(
        shown.camera.host,
        "device host",
        &recipe.host,
        "Use --host or set the camera host in Settings.",
    )?;
    if let LiveOverlay::Mqtt { broker_host, .. } = &recipe.overlay {
        refusal(
            shown.broker.host,
            "MQTT broker host",
            broker_host,
            "Use --mqtt-host or set the broker host in Settings.",
        )?;
    }
    Ok(())
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
