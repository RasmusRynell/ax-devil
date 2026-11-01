//! The command line: the local backend (bare `ax-devil`, or `local` to open a video), `serve` for other machines, and
//! `app`, which opens the desktop shell.

use ax_devil::{
    AppState,
    access::{Limits, Policy},
    app,
    share::{self, Hosting, LinkBase, ShareListener},
    video_cache,
    video_sources::VideoSources,
};
use axum::Router;
use clap::{Args, Parser, Subcommand};
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

/// How long open connections, such as the event stream, get to close after Ctrl+C before the backend exits anyway.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(2);

/// The environment variable that sets the server password for `serve`.
const PASSWORD_VARIABLE: &str = "AX_DEVIL_SERVER_PASSWORD";

/// Desktop app for inspecting Axis camera streams, offline video, and analytics overlays.
#[derive(Parser)]
#[command(version)]
struct Cli {
    /// Use this configuration file instead of ~/.ax_devil/configs/default.json.
    #[arg(long, global = true, value_name = "PATH")]
    config: Option<PathBuf>,
    /// Keep caches in this folder instead of ~/.ax_devil/cache.
    #[arg(long, global = true, value_name = "PATH")]
    cache_dir: Option<PathBuf>,
    /// How much to log.
    #[arg(
        long,
        global = true,
        value_name = "DEBUG|INFO|WARNING|ERROR",
        default_value = "INFO"
    )]
    log_level: logging::Level,
    /// Where the desktop backend listens for other machines while a session exists; tests use a free port.
    #[arg(
        long,
        hide = true,
        value_name = "ADDRESS",
        default_value = "0.0.0.0:7340"
    )]
    share_address: SocketAddr,
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand)]
enum Command {
    /// Open an offline video file in a new Untitled workspace.
    ///
    /// A file that cannot be read stays listed in the workspace, with the reason.
    Local {
        /// The video file.
        #[arg(long, value_name = "PATH")]
        video: PathBuf,
    },
    /// Serve the app to other machines, protected by a server password.
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

/// Where the backend keeps its configuration and caches.
struct Folders {
    config: PathBuf,
    cache: PathBuf,
}

impl Folders {
    /// The folders the command line names, and `~/.ax_devil` for the rest; `None` when the home folder is needed
    /// and unknown.
    fn resolve(
        home: Option<PathBuf>,
        config: Option<PathBuf>,
        cache: Option<PathBuf>,
    ) -> Option<Self> {
        match (config, cache, home) {
            (Some(config), Some(cache), _) => Some(Self { config, cache }),
            (config, cache, Some(home)) => Some(Self {
                config: config.unwrap_or_else(|| home.join("configs/default.json")),
                cache: cache.unwrap_or_else(|| home.join("cache")),
            }),
            _ => None,
        }
    }
}

/// Runs the command the user gave.
pub async fn run() -> ExitCode {
    let cli = Cli::parse();
    let home = std::env::home_dir().map(|home| home.join(".ax_devil"));
    let launcher = matches!(cli.command, Some(Command::App(_)));
    let log_file = home.as_ref().map(|home| home.join("logs/ax-devil.log"));
    logging::init(cli.log_level, log_file.as_deref().filter(|_| !launcher));
    let result = match cli.command {
        Some(Command::App(args)) => launch(&args),
        command => match Folders::resolve(home, cli.config, cli.cache_dir) {
            None => Err("Cannot find the home folder".into()),
            Some(folders) => match command {
                Some(Command::Serve(args)) => serve(&folders, args).await,
                Some(Command::Local { video }) => match workspace::intake::video(&video) {
                    Ok(item) => host_desktop(&folders, cli.share_address, vec![item]).await,
                    Err(error) => Err(format!("{}: {}", error.location, error.reason).into()),
                },
                _ => host_desktop(&folders, cli.share_address, Vec::new()).await,
            },
        },
    };
    result.unwrap_or_else(|error| {
        tracing::error!("{error}");
        ExitCode::FAILURE
    })
}

/// What both backends start from: the services their routes are adapters over, and the stop signal.
struct Services {
    settings: Arc<settings::Settings>,
    store: workspace_store::Store,
    videos: Arc<VideoSources>,
    stop: watch::Sender<bool>,
}

impl Services {
    /// Loads the settings, starts the video cache and fills the workspace with `items`. Ctrl+C turns the stop
    /// signal on.
    fn start(
        folders: &Folders,
        items: Vec<workspace::WorkspaceItem>,
    ) -> Result<Self, Box<dyn Error>> {
        let settings = Arc::new(settings::Settings::load(&folders.config)?);
        tracing::info!("Loaded settings from {}", folders.config.display());
        let videos = video_cache::start(&settings, &folders.cache);
        let store = workspace_store::Store::new(tokio::runtime::Handle::current());
        store.add_items(items, true);
        let (stop, _) = watch::channel(false);
        let stop_on_ctrl_c = stop.clone();
        tokio::spawn(async move {
            let _ = tokio::signal::ctrl_c().await;
            stop_on_ctrl_c.send_replace(true);
        });
        Ok(Self {
            settings,
            store,
            videos,
            stop,
        })
    }

    /// The state of one listener over these services.
    fn state(&self, policy: Policy) -> AppState {
        AppState::new(
            Arc::clone(&self.settings),
            policy,
            self.store.clone(),
            Arc::clone(&self.videos),
            self.stop.subscribe(),
        )
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
    folders: &Folders,
    share_address: SocketAddr,
    items: Vec<workspace::WorkspaceItem>,
) -> Result<ExitCode, Box<dyn Error>> {
    let services = Services::start(folders, items)?;
    let port = std::env::var("PORT")
        .unwrap_or_else(|_| "7330".into())
        .parse::<u16>()?;
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    // The desktop shell and the development supervisor wait for this exact line.
    announce(&[format!("ax-devil: http://{}", listener.local_addr()?)]);
    let state = services.state(Policy::Local);
    // The listener for other machines serves the sessions of this state and opens with the first of them.
    let hosting = Hosting::Lan(ShareListener::new(share_address, &state));
    services
        .serve(listener, app(state.with_hosting(hosting)))
        .await?;
    Ok(ExitCode::SUCCESS)
}

/// The backend for other machines. It reads the same settings file, but only the Server settings are reachable.
async fn serve(folders: &Folders, args: ServeArgs) -> Result<ExitCode, Box<dyn Error>> {
    share::refuse_development_build().map_err(|reason| format!("serve: {reason}"))?;
    let services = Services::start(folders, Vec::new())?;
    let chosen = std::env::var(PASSWORD_VARIABLE)
        .ok()
        .filter(|text| !text.is_empty());
    let generated = chosen.is_none();
    let password = match chosen {
        Some(text) => Secret::chosen(text),
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
    let desktop = match std::env::var_os("AX_DEVIL_DESKTOP") {
        Some(folder) => PathBuf::from(folder),
        // target/<profile>/ax-devil sits three levels below the checkout that holds desktop/.
        None => std::env::current_exe()?
            .ancestors()
            .nth(3)
            .map(|root| root.join("desktop"))
            .unwrap_or_default(),
    };
    let binary = if cfg!(windows) {
        "electron.cmd"
    } else {
        "electron"
    };
    let electron = desktop.join("node_modules/.bin").join(binary);
    if !electron.is_file() {
        return Err(format!(
            "Cannot find the desktop shell's Electron at {}; set AX_DEVIL_DESKTOP to the desktop folder, \
             installed with pnpm install",
            electron.display()
        )
        .into());
    }
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
