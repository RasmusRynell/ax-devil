//! The command line: the local backend (bare `ax-devil`, or `local` and `playlist` to open something), `serve` for
//! other machines, `app`, which opens the desktop shell, and the commands that only print.
//!
//! `args` builds the parser from what the app knows (the `playlist` subcommands come from each resolver's settings
//! schema); the items the command line asks for wait in the [`LaunchQueue`] for the first Local session.

use crate::paths::{self, Paths};
use api_types::handlers::PluginsInfo;
use ax_devil::{
    AppState,
    access::{Limits, Policy},
    app, desktop,
    session::LaunchQueue,
    share::{self, Hosting, LinkBase, ShareListener},
    video_cache,
    video_sources::VideoSources,
};
use axum::Router;
use catalogs::Library;
use clap::{Args, Parser, Subcommand, ValueEnum, ValueHint};
use handler_api::Settings;
use handlers::Handlers;
use plugins::PluginsCommand;
use sessions::Secret;
use std::{
    error::Error,
    future::IntoFuture,
    net::{IpAddr, SocketAddr},
    path::PathBuf,
    process::{Command as Process, ExitCode},
    sync::Arc,
    time::Duration,
};
use tokio::sync::watch;
use workspace::WorkspaceItem;

mod args;
mod catalog;
mod launch;
use launch::{describe, launch_items};
mod plugins;
#[cfg(test)]
mod tests;

/// How long open connections, such as the sockets, get to close after Ctrl+C before the backend exits anyway.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(2);

/// Set to `1` by the desktop shell: the backend then stops when its standard input closes, so it never outlives the
/// shell, even one that crashed. Unset, a closed standard input (a service, a container) changes nothing.
const EXIT_ON_STDIN_CLOSE_VARIABLE: &str = "AX_DEVIL_EXIT_ON_STDIN_CLOSE";

/// The environment variable that sets the server password for `serve`: printable ASCII without spaces, because a
/// browser can send nothing else in a header. Unset or empty, a random password is generated.
const PASSWORD_VARIABLE: &str = "AX_DEVIL_SERVER_PASSWORD";

/// Desktop app for inspecting Axis camera streams, offline video, and analytics overlays.
#[derive(Parser)]
#[command(version)]
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

/// What `catalog` was asked to do.
#[derive(Subcommand)]
enum CatalogCommand {
    /// List the built-in catalogs and the files of the user's catalog folder.
    List,
    /// Validate a catalog file; every problem is listed with its JSON location.
    Check {
        /// The catalog file, or a catalog of the user's folder as `user:<file name>`.
        #[arg(value_name = "FILE", value_hint = ValueHint::FilePath)]
        file: PathBuf,
    },
    /// Draw a sheet of example objects with a catalog and write it as a PNG; needs no display.
    ///
    /// The catalog is checked first, and a catalog with problems is listed as `check` does.
    Render {
        /// The catalog file, or a catalog of the user's folder as `user:<file name>`.
        #[arg(value_name = "FILE", value_hint = ValueHint::FilePath)]
        file: PathBuf,
        /// The folder to write the picture to; made when missing. The file is named after the sheet.
        #[arg(long, value_name = "DIR", value_hint = ValueHint::DirPath)]
        out: PathBuf,
        /// The sheet to draw.
        #[arg(long, value_enum, default_value_t = SheetName::Overview)]
        sheet: SheetName,
    },
}

/// The example sheets `catalog render` can draw.
#[derive(Clone, Copy, ValueEnum)]
enum SheetName {
    /// One object for every class the built-in decoders name, and a relation.
    Overview,
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
    let (handlers, installed) = ax_devil::plugins::load(&registry, Handlers::built_in());
    let Cli { global, command } = args::parse(&handlers);
    // These commands only print: no logging, settings file or port.
    match command {
        Some(Command::ListHandlers) => catalog::Report::listing(&handlers, &installed).print(),
        Some(Command::Plugins(command)) => plugins::run(command, registry, handlers, installed)
            .await
            .print(),
        Some(Command::Catalog(catalog)) => {
            let folder = Paths::resolve(
                &|name| std::env::var_os(name),
                global.config,
                global.cache_dir,
            )
            .and_then(|paths| {
                settings::Settings::load(&paths.config_file)
                    .map(|settings| catalog_folder(&paths, &settings))
                    .map_err(|error| error.to_string())
            });
            catalog::run(&catalog, folder).print()
        }
        command => run_backend(global, command, handlers, installed).await,
    }
}

/// Starts what `command` asks for: the desktop backend, `serve` or the shell.
async fn run_backend(
    global: Global,
    command: Option<Command>,
    handlers: Handlers,
    installed: PluginsInfo,
) -> ExitCode {
    let paths = Paths::resolve(
        &|name| std::env::var_os(name),
        global.config,
        global.cache_dir,
    );
    let launcher = matches!(command, Some(Command::App(_)));
    let log_file = paths.as_ref().ok().map(|paths| paths.log_file.as_path());
    logging::init(global.log_level, log_file.filter(|_| !launcher));
    ax_devil::plugins::log_problems(&installed);
    let result = match command {
        Some(Command::App(args)) => launch(&args),
        command => match paths {
            Err(error) => Err(error.into()),
            Ok(paths) => match command {
                Some(Command::Serve(args)) => serve(&paths, args, handlers, installed).await,
                command => match launch_items(command, &handlers) {
                    Ok(items) => {
                        host_desktop(&paths, global.share_address, handlers, installed, items).await
                    }
                    Err(reason) => Err(reason.into()),
                },
            },
        },
    };
    result.unwrap_or_else(|error| {
        tracing::error!("{error}");
        ExitCode::FAILURE
    })
}

/// The folder of the user's render catalogs: the `storage.catalog_dir` setting, else the standard folder.
fn catalog_folder(paths: &Paths, settings: &settings::Settings) -> PathBuf {
    paths.catalog_dir(settings.state().folder("storage.catalog_dir"))
}

/// What both backends start from: the services their routes are adapters over, and the stop signal.
struct Services {
    settings: Arc<settings::Settings>,
    catalogs: Library,
    launch: Arc<LaunchQueue>,
    videos: Arc<VideoSources>,
    handlers: Handlers,
    plugins: PluginsInfo,
    cache_dir: PathBuf,
    stop: watch::Sender<bool>,
}

impl Services {
    /// Loads the settings, starts the video cache and queues `items` for the first Local session. Ctrl+C turns the
    /// stop signal on, and so does the end of the standard input when [`EXIT_ON_STDIN_CLOSE_VARIABLE`] asks for it.
    fn start(
        paths: &Paths,
        handlers: Handlers,
        plugins: PluginsInfo,
        items: Vec<WorkspaceItem>,
    ) -> Result<Self, Box<dyn Error>> {
        let settings = Arc::new(settings::Settings::load(&paths.config_file)?);
        tracing::info!("Loaded settings from {}", paths.config_file.display());
        let cache_dir = paths.cache_dir(settings.state().folder("storage.cache_dir"));
        tracing::info!("Caching in {}", cache_dir.display());
        let videos = video_cache::start(&settings, &cache_dir);
        let catalog_dir = catalog_folder(paths, &settings);
        tracing::info!("Reading user catalogs from {}", catalog_dir.display());
        let catalogs = Library::with_folder(catalog_dir);
        let (stop, _) = watch::channel(false);
        let stop_on_ctrl_c = stop.clone();
        tokio::spawn(async move {
            let _ = tokio::signal::ctrl_c().await;
            stop_on_ctrl_c.send_replace(true);
        });
        if std::env::var_os(EXIT_ON_STDIN_CLOSE_VARIABLE).is_some_and(|value| value == "1") {
            let stop_on_eof = stop.clone();
            // A plain thread: reading standard input blocks, and the thread may stay blocked until the process exits.
            std::thread::spawn(move || {
                let _ = std::io::copy(&mut std::io::stdin().lock(), &mut std::io::sink());
                stop_on_eof.send_replace(true);
            });
        }
        Ok(Self {
            settings,
            catalogs,
            launch: Arc::new(LaunchQueue::new(items)),
            videos,
            handlers,
            plugins,
            cache_dir,
            stop,
        })
    }

    /// The state of one listener over these services.
    fn state(&self, policy: Policy) -> AppState {
        AppState::new(
            Arc::clone(&self.settings),
            policy,
            Arc::clone(&self.videos),
            self.stop.subscribe(),
            self.handlers.clone(),
            &self.cache_dir,
        )
        .with_plugins(self.plugins.clone())
        .with_launch(Arc::clone(&self.launch))
        .with_catalogs(self.catalogs.clone())
    }

    /// Serves `router` until the stop signal, giving connections [`SHUTDOWN_GRACE`] to close, then ends every viewer
    /// and waits once for the decode threads to finish their frame and exit.
    async fn serve(
        self,
        listener: tokio::net::TcpListener,
        router: Router,
    ) -> Result<(), Box<dyn Error>> {
        let (mut graceful, mut expired) = (self.stop.subscribe(), self.stop.subscribe());
        let server = axum::serve(
            listener,
            router.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .with_graceful_shutdown(async move {
            let _ = graceful.wait_for(|stop| *stop).await;
        });
        let served = tokio::select! {
            served = server.into_future() => served,
            () = async {
                let _ = expired.wait_for(|stop| *stop).await;
                tokio::time::sleep(SHUTDOWN_GRACE).await;
            } => {
                tracing::warn!("Closing connections that stayed open after Ctrl+C");
                Ok(())
            }
        };
        // Whatever ended the server, every viewer ends.
        self.stop.send_replace(true);
        let videos = self.videos;
        let retired =
            tokio::task::spawn_blocking(move || videos.registry().wait_retired(SHUTDOWN_GRACE))
                .await;
        if !matches!(retired, Ok(true)) {
            tracing::warn!("Some video decoders did not stop in time");
        }
        Ok(served?)
    }
}

/// The backend for the desktop shell and development: loopback only, on `$PORT` (default 7330).
async fn host_desktop(
    paths: &Paths,
    share_address: SocketAddr,
    handlers: Handlers,
    plugins: PluginsInfo,
    items: Vec<WorkspaceItem>,
) -> Result<ExitCode, Box<dyn Error>> {
    let services = Services::start(paths, handlers, plugins, items)?;
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
    handlers: Handlers,
    plugins: PluginsInfo,
) -> Result<ExitCode, Box<dyn Error>> {
    share::refuse_development_build().map_err(|reason| format!("serve: {reason}"))?;
    let services = Services::start(paths, handlers, plugins, Vec::new())?;
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
fn launch(args: &AppArgs) -> Result<ExitCode, Box<dyn Error>> {
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
