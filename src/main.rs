//! Starts the backend on 127.0.0.1:$PORT (default 7330) and stops it on Ctrl+C.

use clap::{Parser, Subcommand};
use std::{
    error::Error,
    future::IntoFuture,
    path::{Path, PathBuf},
    process::ExitCode,
    sync::Arc,
    time::Duration,
};

/// How long open connections, such as the event stream, get to close after Ctrl+C before the backend exits anyway.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(2);

/// How long the runtime waits for blocking work, such as indexing a video, once everything else has stopped.
const RUNTIME_GRACE: Duration = Duration::from_secs(1);

/// Desktop app for inspecting Axis camera streams, offline video, and analytics overlays.
#[derive(Parser)]
#[command(version)]
struct Cli {
    /// Use this configuration file instead of ~/.ax_devil/configs/default.json.
    #[arg(long, value_name = "PATH")]
    config: Option<PathBuf>,
    /// Keep caches in this folder instead of ~/.ax_devil/cache.
    #[arg(long, value_name = "PATH")]
    cache_dir: Option<PathBuf>,
    /// How much to log.
    #[arg(long, value_name = "DEBUG|INFO|WARNING|ERROR", default_value = "INFO")]
    log_level: logging::Level,
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
}

fn main() -> ExitCode {
    match ax_devil::runtime::run(run(), RUNTIME_GRACE) {
        Ok(code) => code,
        Err(error) => {
            // Logging starts inside `run`, which never ran.
            #[allow(clippy::print_stderr)]
            {
                eprintln!("ax-devil: cannot start the runtime: {error}");
            }
            ExitCode::FAILURE
        }
    }
}

async fn run() -> ExitCode {
    let cli = Cli::parse();
    let Some(home) = std::env::home_dir().map(|home| home.join(".ax_devil")) else {
        logging::init(cli.log_level, None);
        tracing::error!("Cannot find the home folder");
        return ExitCode::FAILURE;
    };
    logging::init(cli.log_level, Some(&home.join("logs/ax-devil.log")));
    let config = cli
        .config
        .unwrap_or_else(|| home.join("configs/default.json"));
    let cache_dir = cli.cache_dir.unwrap_or_else(|| home.join("cache"));
    let items = match &cli.command {
        None => Vec::new(),
        Some(Command::Local { video }) => match workspace::intake::video(video) {
            Ok(item) => vec![item],
            Err(error) => {
                tracing::error!("{}: {}", error.location, error.reason);
                return ExitCode::FAILURE;
            }
        },
    };
    match serve(&config, cache_dir, items).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            tracing::error!("{error}");
            ExitCode::FAILURE
        }
    }
}

async fn serve(
    config: &Path,
    cache_dir: PathBuf,
    items: Vec<workspace::WorkspaceItem>,
) -> Result<(), Box<dyn Error>> {
    let settings = Arc::new(settings::Settings::load(config)?);
    tracing::info!("Loaded settings from {}", config.display());
    let videos = ax_devil::video_cache::start(&settings, &cache_dir);
    let store = workspace_store::Store::new(tokio::runtime::Handle::current());
    store.add_items(items, true);
    let port = std::env::var("PORT")
        .unwrap_or_else(|_| "7330".into())
        .parse::<u16>()?;
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    // The desktop shell and the development supervisor wait for this exact line.
    #[allow(clippy::print_stdout)]
    {
        println!("ax-devil: http://{}", listener.local_addr()?);
    }
    let (stop, stopped) = tokio::sync::watch::channel(false);
    let stop = Arc::new(stop);
    let on_ctrl_c = Arc::clone(&stop);
    tokio::spawn(async move {
        let _ = tokio::signal::ctrl_c().await;
        on_ctrl_c.send_replace(true);
    });
    let (mut graceful, mut expired) = (stopped.clone(), stopped);
    let server = axum::serve(
        listener,
        ax_devil::app(ax_devil::AppState {
            settings,
            store,
            videos: Arc::clone(&videos),
            // Open playback sockets end when this turns true, so they cannot hold the shutdown or keep
            // decoders running.
            stopping: graceful.clone(),
        }),
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
    // Whatever ended the server, every viewer ends, and the decode threads get one bounded wait to finish their
    // frame and exit.
    stop.send_replace(true);
    let retired =
        tokio::task::spawn_blocking(move || videos.registry().wait_retired(SHUTDOWN_GRACE)).await;
    if !matches!(retired, Ok(true)) {
        tracing::warn!("Some video decoders did not stop in time");
    }
    Ok(served?)
}
