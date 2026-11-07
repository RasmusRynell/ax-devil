//! `plugins`: install, update, remove, list and check plugins. These commands only print, so they need no server,
//! settings or log file. A running ax-devil picks the change up within a second or two.

use super::{catalog::Report, describe};
use api_types::handlers::{PluginSource, PluginStatus, PluginsInfo};
use ax_devil::plugins::Plugins;
use clap::{Subcommand, ValueHint};
use foundation::{CancelToken, Interrupt, UserError};
use handler_api::{OverlayRef, Settings};
use handlers::Handlers;
use scene::SceneTime;
use scene_store::{Ingest, TrackRead, TrackRegistry};
use std::{
    fmt::Write,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};

/// How often `check` looks at the ingest.
const POLL: Duration = Duration::from_millis(20);

/// What `plugins` was asked to do.
#[derive(Subcommand)]
pub enum PluginsCommand {
    /// Install the plugin in a folder, or by its manifest file.
    Install {
        /// The folder holding ax-devil-plugin.json, or that file.
        #[arg(value_name = "PATH", value_hint = ValueHint::AnyPath)]
        path: PathBuf,
    },
    /// Replace an installed plugin by the one in another folder or manifest file, after checking it again.
    Update {
        /// The plugin's id, or the path of its folder or manifest when it no longer loads.
        #[arg(value_name = "ID")]
        id: String,
        /// The folder holding ax-devil-plugin.json, or that file.
        #[arg(value_name = "PATH", value_hint = ValueHint::AnyPath)]
        path: PathBuf,
    },
    /// Stop loading a plugin; its files stay where they are.
    Remove {
        /// The plugin's id, or the path of its folder or manifest when it no longer loads.
        #[arg(value_name = "ID")]
        id: String,
    },
    /// List the installed plugins and whether each one loads.
    List {
        /// Print the list as JSON, as `GET /api/plugins` answers.
        #[arg(long)]
        json: bool,
    },
    /// Run an installed plugin's decoder on a file and print what it produced.
    ///
    /// Exits with a failure when the decoder stops early or produces no samples.
    Check {
        /// The decoder's handler id.
        #[arg(value_name = "ID")]
        id: String,
        /// The overlay file.
        #[arg(value_name = "FILE", value_hint = ValueHint::FilePath)]
        file: PathBuf,
        /// An option the decoder declares; repeat for several.
        #[arg(long = "option", value_name = "KEY=VALUE", value_parser = parse_option)]
        options: Vec<(String, String)>,
    },
}

fn parse_option(text: &str) -> Result<(String, String), String> {
    text.split_once('=')
        .map(|(key, value)| (key.to_owned(), value.to_owned()))
        .ok_or_else(|| format!("'{text}' is not KEY=VALUE"))
}

fn failed(text: String) -> Report {
    Report {
        text: format!("{text}\n"),
        ok: false,
    }
}

/// What `command` prints. Blocking work, such as a check, runs off the async workers and is cancelled by Ctrl+C,
/// which stops the plugin.
pub async fn run(command: PluginsCommand, plugins: Arc<Plugins>) -> Report {
    let cancel = CancelToken::new();
    let on_ctrl_c = cancel.clone();
    tokio::spawn(async move {
        let _ = tokio::signal::ctrl_c().await;
        on_ctrl_c.cancel();
    });
    let job = tokio::task::spawn_blocking(move || match command {
        PluginsCommand::List { json } => list(&plugins.info(), json),
        PluginsCommand::Install { path } => changed(plugins.install(&path), |info| {
            // An install goes last.
            match info.plugins.last().map(|plugin| &plugin.status) {
                Some(PluginStatus::Loaded { id, version, .. }) => {
                    format!("Installed {id} {version}.")
                }
                _ => "Installed.".to_owned(),
            }
        }),
        PluginsCommand::Update { id, path } => {
            changed(plugins.update(&id, &path), |_| format!("Updated {id}."))
        }
        PluginsCommand::Remove { id } => changed(plugins.remove(&id), |_| {
            format!("Removed {id}; its files were left.")
        }),
        PluginsCommand::Check { id, file, options } => {
            let info = plugins.info();
            if provided_by_installed(&info, &id) {
                check(&plugins.handlers(), &id, &file, options, &cancel)
            } else {
                failed(format!(
                    "No installed plugin provides the decoder '{id}'; `ax-devil plugins list` shows what is installed"
                ))
            }
        }
    });
    job.await
        .unwrap_or_else(|error| failed(format!("The command stopped unexpectedly: {error}")))
}

/// Whether a loaded installed plugin lists the handler `id`.
fn provided_by_installed(info: &PluginsInfo, id: &str) -> bool {
    info.plugins.iter().any(|plugin| {
        matches!(plugin.source, PluginSource::Installed { .. })
            && matches!(&plugin.status, PluginStatus::Loaded { handlers, .. }
                if handlers.iter().any(|handler| handler.id == id))
    })
}

/// The report of a change to the registry: `done` words the new state when it succeeded.
fn changed(
    result: Result<Arc<PluginsInfo>, UserError>,
    done: impl FnOnce(&PluginsInfo) -> String,
) -> Report {
    match result {
        Ok(info) => Report {
            text: format!("{}\n", done(&info)),
            ok: true,
        },
        Err(error) => failed(error.reason),
    }
}

fn list(loaded: &PluginsInfo, json: bool) -> Report {
    if json {
        return Report {
            text: format!(
                "{}\n",
                serde_json::to_string_pretty(loaded).unwrap_or_default()
            ),
            ok: loaded.problem.is_none(),
        };
    }
    if let Some(problem) = &loaded.problem {
        return failed(problem.clone());
    }
    let mut text = String::new();
    for plugin in &loaded.plugins {
        let PluginSource::Installed { manifest, folder } = &plugin.source else {
            continue;
        };
        let line = match &plugin.status {
            PluginStatus::Loaded {
                id,
                version,
                handlers,
                skipped,
                ..
            } => {
                let extensions: Vec<_> = handlers
                    .iter()
                    .flat_map(|handler| handler.extensions.iter().map(String::as_str))
                    .collect();
                let mut line = format!("{id}  {version}  {}  {folder}", extensions.join(", "));
                for handler in skipped {
                    let id = handler.id.as_deref().unwrap_or("-");
                    line.push_str(&format!(
                        "\n  skipped  {}  {id}  {}",
                        handler.kind, handler.reason
                    ));
                }
                line
            }
            PluginStatus::Failed { reason } => format!("not loaded  {manifest}  {reason}"),
        };
        text.push_str(&line);
        text.push('\n');
    }
    if text.is_empty() {
        text.push_str("No plugins are installed.\n");
    }
    Report { text, ok: true }
}

/// Runs the plugin decoder `id` on `file` into a private store and says what came out.
fn check(
    handlers: &Handlers,
    id: &str,
    file: &Path,
    options: Vec<(String, String)>,
    cancel: &CancelToken,
) -> Report {
    let overlay = OverlayRef {
        label: id.to_owned(),
        slot: id.to_owned(),
        path: file.to_path_buf(),
        handler: id.to_owned(),
        options: options
            .into_iter()
            .map(|(key, value)| (key, value.into()))
            .collect::<Settings>(),
    };
    let tracks = TrackRegistry::new(handlers.decoder_lookup(), None);
    let track = match tracks.open(&overlay, cancel) {
        Ok(track) => track,
        Err(Interrupt::Failed(error)) => return failed(describe(&error)),
        Err(Interrupt::Cancelled) => return failed("Cancelled".to_owned()),
    };
    let snapshot = loop {
        let snapshot = track.snapshot();
        if snapshot.ingest != Ingest::Reading {
            break snapshot;
        }
        if cancel.is_cancelled() {
            return failed("Cancelled".to_owned());
        }
        std::thread::sleep(POLL);
    };
    let samples = snapshot.keys.len();
    let key_at = |index: usize| match track
        .scene_in(&snapshot, index)
        .map(|scene| scene.time().start())
    {
        Ok(SceneTime::Frame(frame)) => format!("frame {frame}"),
        Ok(SceneTime::Timestamp(time)) => format!("{:.6} s", time.as_us() as f64 / 1e6),
        Err(error) => describe(&error),
    };
    let clock = format!("{:?}", snapshot.clock()).to_lowercase();
    let mut text = format!("{samples} samples, keyed by {clock}\n");
    if samples > 0 {
        let _ = writeln!(text, "first {}, last {}", key_at(0), key_at(samples - 1));
    }
    for warning in snapshot.warnings.iter() {
        let mut reasons = warning.reasons.join("; ");
        if warning.more_reasons {
            reasons.push_str("; ...");
        }
        let reasons = if reasons.is_empty() {
            String::new()
        } else {
            format!(" ({reasons})")
        };
        let _ = writeln!(
            text,
            "warning: {}{reasons} {}",
            warning.problem, warning.consequence
        );
    }
    let ok = match &snapshot.ingest {
        Ingest::Incomplete(error) => {
            let _ = writeln!(text, "incomplete: {}", error.reason);
            false
        }
        _ if samples == 0 => {
            text.push_str("The decoder produced no samples.\n");
            false
        }
        _ => true,
    };
    Report { text, ok }
}
