//! The command line: the local backend (bare `ax-devil`, or `local` and `playlist` to open something), `serve` for
//! other machines, `app`, which opens the desktop shell, and the commands that only print.
//!
//! `args` builds the parser from what the app knows (the `playlist` subcommands come from each resolver's settings
//! schema); the items the command line asks for wait in the [`LaunchQueue`] for the first Local session.

use crate::paths::{self, Paths};
use ax_devil::{
    access::{Limits, Policy},
    app, desktop,
    plugins::Plugins,
    session::Launch,
    share::{self, Hosting, LinkBase, ShareListener},
};
use clap::{Args, Parser, Subcommand, ValueHint};
use handler_api::Settings;
use handlers::Handlers;
use plugins::PluginsCommand;
use sessions::Secret;
use std::{
    error::Error,
    net::{IpAddr, SocketAddr},
    path::PathBuf,
    process::{Command as Process, ExitCode},
    sync::Arc,
};
use workspace_files::RecoveryFolder;

mod args;
mod catalog;
use catalog::CatalogCommand;
mod clear_cache;
mod launch;
use launch::{describe, launch};
mod live;
mod plugins;
mod services;
use services::{Interrupt, Services};
#[cfg(test)]
mod catalog_tests;
#[cfg(test)]
mod startup_tests;
#[cfg(test)]
mod tests;

/// The environment variable that sets the server password for `serve`: printable ASCII without spaces, because a
/// browser can send nothing else in a header. Unset or empty, a random password is generated.
const PASSWORD_VARIABLE: &str = "AX_DEVIL_SERVER_PASSWORD";

/// The links `--help` ends with, which the subcommands' help does not repeat.
const HELP_LINKS: &str = "\
Documentation: https://github.com/RasmusRynell/ax-devil
Agent guidance: https://github.com/RasmusRynell/ax-devil/blob/main/AGENTS.md
Agent skills:   https://github.com/RasmusRynell/ax-devil/tree/main/.agents/skills";

/// Desktop app for inspecting Axis camera streams, offline video, and analytics overlays.
#[derive(Parser)]
#[command(version, after_help = HELP_LINKS)]
struct Cli {
    #[command(flatten)]
    global: Global,
    #[command(subcommand)]
    command: Option<Command>,
}

/// One `--overlay-option`: the key and value as given, and the position of the `--overlay` it belongs to.
#[derive(Clone, Debug)]
struct OverlayOption {
    overlay: usize,
    key: String,
    value: String,
}

/// The options every command takes.
#[derive(Args)]
struct Global {
    /// Use this settings file instead of $XDG_CONFIG_HOME/ax-devil/settings.json (~/.config/ax-devil/settings.json).
    #[arg(long, global = true, value_name = "PATH", value_hint = ValueHint::FilePath)]
    config: Option<PathBuf>,
    /// Keep caches in this folder instead of $XDG_CACHE_HOME/ax-devil (~/.cache/ax-devil).
    #[arg(long, global = true, value_name = "PATH", value_hint = ValueHint::DirPath)]
    cache_dir: Option<PathBuf>,
    /// How much to log.
    #[arg(
        long,
        global = true,
        value_name = "DEBUG|INFO|WARNING|ERROR",
        default_value = "INFO"
    )]
    log_level: logging::Level,
    /// Where the desktop backend listens for other machines while a session is shared; tests use a free port.
    #[arg(
        long,
        hide = true,
        value_name = "ADDRESS",
        default_value = "0.0.0.0:7340"
    )]
    share_address: SocketAddr,
}

#[derive(Subcommand)]
enum Command {
    /// Open an offline video file in a new Untitled workspace.
    ///
    /// A file that cannot be read stays listed in the workspace, with the reason.
    Local {
        /// The video file.
        #[arg(long, value_name = "PATH", value_hint = ValueHint::FilePath)]
        video: PathBuf,
        /// An overlay file drawn on the video; repeat for several.
        #[arg(long, value_name = "PATH", value_hint = ValueHint::FilePath)]
        overlay: Vec<PathBuf>,
        /// The decoder of the --overlay in the same position. Give one for every --overlay or none, which picks each
        /// decoder by file extension.
        #[arg(long, value_name = "HANDLER")]
        handler_type: Vec<String>,
        /// An option of the decoder of the nearest preceding --overlay, as KEY=VALUE; repeat for several. The decoder
        /// declares its options.
        #[arg(long, value_name = "KEY=VALUE", value_parser = args::parse_overlay_option)]
        overlay_option: Vec<OverlayOption>,
    },
    /// Open a live stream in a new Untitled workspace, with the stream settings as defaults.
    Live(live::LiveArgs),
    /// Open a saved workspace.
    Open {
        /// The workspace file, ending in .ax-devil.workspace.
        #[arg(value_name = "FILE", value_hint = ValueHint::FilePath)]
        file: PathBuf,
    },
    /// Open a playlist in a new Untitled workspace.
    ///
    /// Only `args` makes this one, from the subcommand of a resolver, so the derive skips it.
    #[command(skip)]
    Playlist {
        /// The resolver id.
        resolver: String,
        /// The settings its arguments gave, keyed as the resolver's schema declares.
        settings: Settings,
    },
    /// List the overlay decoders and playlist resolvers, then exit.
    ListHandlers,
    /// Remove the cached frame indexes, decoded overlays and thumbnails, then exit.
    ClearCache {
        /// Remove them without asking.
        #[arg(long)]
        force: bool,
    },
    /// List the render catalogs, check a catalog file or draw one, then exit.
    #[command(subcommand)]
    Catalog(CatalogCommand),
    /// Install, remove, list or check plugins, which read overlay files of other formats, then exit.
    #[command(subcommand)]
    Plugins(PluginsCommand),
    /// Serve the app to other machines, protected by a server password.
    ///
    /// AX_DEVIL_SERVER_PASSWORD chooses the password (printable ASCII, no spaces); otherwise one is generated.
    Serve(ServeArgs),
    /// Open the desktop app, optionally on another machine's server.
    App(AppArgs),
}

#[derive(Args)]
struct ServeArgs {
    /// The address to listen on.
    #[arg(long, default_value = "127.0.0.1")]
    bind: IpAddr,
    /// The port to listen on; 0 picks a free one.
    #[arg(long, default_value_t = 7340)]
    port: u16,
    /// The address other machines use to reach this server, when a reverse proxy sits in front of it.
    #[arg(long, value_name = "URL", value_parser = LinkBase::parse_public)]
    public_url: Option<LinkBase>,
    /// Count open connections for the whole server only, not per client address: every client of a reverse proxy
    /// has the proxy's address.
    #[arg(long)]
    behind_proxy: bool,
}

#[derive(Args)]
struct AppArgs {
    /// Load this server's frontend instead of starting the local backend.
    #[arg(long, value_name = "URL")]
    server: Option<String>,
}

/// Runs the command the user gave.
pub async fn run() -> ExitCode {
    let registry = paths::plugins_file(&|name| std::env::var_os(name));
    let plugins = Plugins::new(registry, Handlers::built_in());
    let Cli { global, command } = args::parse(&plugins.handlers());
    // These commands only print: no logging, settings file or port.
    match command {
        Some(Command::ListHandlers) => {
            catalog::Report::listing(&plugins.handlers(), &plugins.info()).print()
        }
        Some(Command::ClearCache { force }) => {
            let folder = settings_folder(global, |paths, settings| {
                paths.cache_dir(settings.state().folder("storage.cache_dir"))
            });
            clear_cache::run(folder, force)
        }
        Some(Command::Plugins(command)) => plugins::run(command, plugins).await.print(),
        Some(Command::Catalog(catalog)) => catalog::run(
            &catalog,
            catalog::Home::resolve(global.config, global.cache_dir),
        )
        .print(),
        command => run_backend(global, command, plugins).await,
    }
}

/// A folder that depends on the settings file: resolves the paths of `global`, loads the settings and applies `pick`.
fn settings_folder(
    global: Global,
    pick: impl FnOnce(&Paths, &settings::Settings) -> PathBuf,
) -> Result<PathBuf, String> {
    let paths = Paths::resolve(
        &|name| std::env::var_os(name),
        global.config,
        global.cache_dir,
    )?;
    let settings =
        settings::Settings::load(&paths.config_file).map_err(|error| error.to_string())?;
    Ok(pick(&paths, &settings))
}

/// Starts what `command` asks for: the desktop backend, `serve` or the shell.
async fn run_backend(global: Global, command: Option<Command>, plugins: Arc<Plugins>) -> ExitCode {
    let interrupt = Interrupt::watch();
    let paths = Paths::resolve(
        &|name| std::env::var_os(name),
        global.config,
        global.cache_dir,
    );
    let launcher = matches!(command, Some(Command::App(_)));
    let log_file = paths.as_ref().ok().map(|paths| paths.log_file.as_path());
    logging::init(global.log_level, log_file.filter(|_| !launcher));
    ax_devil::plugins::log_problems(&plugins.info());
    let (prefix, result) = match command {
        Some(Command::App(args)) => ("Application failed to start: ", launch_app(&args)),
        command => match paths {
            Err(error) => ("", Err(error.into())),
            Ok(paths) => match command {
                Some(Command::Serve(args)) => (
                    "Application failed to start: ",
                    serve(&paths, args, plugins, &interrupt).await,
                ),
                command => match launch(
                    command,
                    &plugins.handlers(),
                    &RecoveryFolder::new(paths.recovery_dir.clone()),
                    || {
                        settings::Settings::load(&paths.config_file)
                            .map(|settings| ax_devil::session::live::defaults(&settings.state()))
                            .map_err(|error| error.to_string())
                    },
                ) {
                    Ok(launch) => (
                        "Application failed to start: ",
                        host_desktop(&paths, global.share_address, plugins, launch, &interrupt)
                            .await,
                    ),
                    Err(reason) => ("", Err(reason.into())),
                },
            },
        },
    };
    finish(&interrupt, prefix, result)
}

/// The exit status of the run. Ctrl+C decides it, and an error that came with or after it is only detail; otherwise
/// it is the run's own status, or failure with the reason logged under `prefix`.
fn finish(
    interrupt: &Interrupt,
    prefix: &str,
    result: Result<ExitCode, Box<dyn Error>>,
) -> ExitCode {
    let Some(interrupted) = interrupt.status() else {
        return result.unwrap_or_else(|error| fail(prefix, error));
    };
    // Error level, so every --log-level shows it, as Python prints it whatever the level.
    tracing::error!("Application interrupted by user");
    if let Err(error) = result {
        tracing::debug!("{prefix}{error}");
        if let Some(causes) = causes(error.as_ref()) {
            tracing::debug!("{causes}");
        }
    }
    interrupted
}

/// Logs `error` after `prefix`, then its chain of causes at DEBUG, which is where Python shows its traceback.
fn fail(prefix: &str, error: Box<dyn Error>) -> ExitCode {
    tracing::error!("{prefix}{error}");
    if let Some(causes) = causes(error.as_ref()) {
        tracing::debug!("{causes}");
    }
    ExitCode::FAILURE
}

/// What caused `error`, one line for each link of its source chain; none when it has no source.
fn causes(error: &dyn Error) -> Option<String> {
    let lines: Vec<String> = std::iter::successors(error.source(), |cause| (*cause).source())
        .map(|cause| format!("caused by: {cause}"))
        .collect();
    (!lines.is_empty()).then(|| lines.join("\n"))
}

/// The folder of the user's render catalogs: the `storage.catalog_dir` setting, else the standard folder.
fn catalog_folder(paths: &Paths, settings: &settings::Settings) -> PathBuf {
    paths.catalog_dir(settings.state().folder("storage.catalog_dir"))
}

/// The backend for the desktop shell and development: loopback only, on `$PORT` (default 7330).
async fn host_desktop(
    paths: &Paths,
    share_address: SocketAddr,
    plugins: Arc<Plugins>,
    launch: Option<Launch>,
    interrupt: &Interrupt,
) -> Result<ExitCode, Box<dyn Error>> {
    let services = Services::start(paths, plugins, launch, interrupt)?;
    let port = std::env::var("PORT")
        .unwrap_or_else(|_| "7330".into())
        .parse::<u16>()?;
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    // The desktop shell and the development supervisor wait for this exact line.
    announce(&[format!("ax-devil: http://{}", listener.local_addr()?)]);
    let state = services
        .state(Policy::Local)
        .with_windows(paths.window_file.clone());
    // The listener for other machines serves the sessions of this state and opens when one starts sharing.
    let hosting = Hosting::Lan(ShareListener::new(share_address, &state));
    services
        .serve(listener, app(state.with_hosting(hosting)))
        .await?;
    Ok(ExitCode::SUCCESS)
}

/// The backend for other machines. It reads the same settings file, but only the Server settings are reachable.
async fn serve(
    paths: &Paths,
    args: ServeArgs,
    plugins: Arc<Plugins>,
    interrupt: &Interrupt,
) -> Result<ExitCode, Box<dyn Error>> {
    share::refuse_development_build().map_err(|reason| format!("serve: {reason}"))?;
    let services = Services::start(paths, plugins, None, interrupt)?;
    let chosen = std::env::var(PASSWORD_VARIABLE)
        .ok()
        .filter(|text| !text.is_empty());
    let generated = chosen.is_none();
    let password = match chosen {
        Some(text) => {
            Secret::chosen(text).map_err(|reason| format!("{PASSWORD_VARIABLE}: {reason}"))?
        }
        None => Secret::generate(),
    };
    let listener = tokio::net::TcpListener::bind((args.bind, args.port)).await?;
    let bound = listener.local_addr()?;
    let base = LinkBase::for_listener(args.public_url, bound);
    if !bound.ip().is_loopback() && !base.is_https() {
        tracing::warn!(
            "Serving plain HTTP on {bound}: the password and everything shown cross the network unencrypted. \
             Use a trusted network, or put a TLS reverse proxy in front and pass --public-url https://..."
        );
    }
    announce(&if generated {
        vec![
            format!("ax-devil server password: {password}"),
            format!("ax-devil: {}", base.link(&format!("password={password}"))),
        ]
    } else {
        vec![format!("ax-devil: {}", base.url())]
    });
    let limits = if args.behind_proxy {
        Limits::behind_proxy()
    } else {
        Limits::default()
    };
    let state = services
        .state(Policy::Shared {
            password: Some(password),
        })
        .with_limits(limits)
        .with_hosting(Hosting::Server(base));
    services.serve(listener, app(state)).await?;
    Ok(ExitCode::SUCCESS)
}

/// Starts the desktop shell and waits for it, passing on its exit code.
fn launch_app(args: &AppArgs) -> Result<ExitCode, Box<dyn Error>> {
    let desktop = desktop::folder()?;
    let electron = desktop::electron(&desktop)?;
    let mut shell = Process::new(electron);
    // Shells hosted in an Electron app export this, which would start Electron as plain Node and crash it at once.
    shell.env_remove("ELECTRON_RUN_AS_NODE");
    shell.arg(&desktop);
    if let Some(server) = &args.server {
        shell.args(["--server", server]);
    }
    let status = shell.status()?;
    Ok(status
        .code()
        .and_then(|code| u8::try_from(code).ok())
        .map_or(ExitCode::FAILURE, ExitCode::from))
}

/// Prints the lines other programs and the operator read from stdout.
#[allow(clippy::print_stdout)]
fn announce(lines: &[String]) {
    for line in lines {
        println!("{line}");
    }
}
