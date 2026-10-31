//! Starts the backend on 127.0.0.1:$PORT (default 7330) and stops it on Ctrl+C.

use clap::Parser;
use std::{error::Error, path::PathBuf, process::ExitCode};

/// Desktop app for inspecting Axis camera streams, offline video, and analytics overlays.
#[derive(Parser)]
#[command(version)]
struct Cli {
    /// Use this configuration file instead of ~/.ax_devil/configs/default.json.
    #[arg(long, value_name = "PATH")]
    config: Option<PathBuf>,
    /// How much to log.
    #[arg(long, value_name = "DEBUG|INFO|WARNING|ERROR", default_value = "INFO")]
    log_level: logging::Level,
}

#[tokio::main]
async fn main() -> ExitCode {
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
    match serve(&config).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            tracing::error!("{error}");
            ExitCode::FAILURE
        }
    }
}

async fn serve(config: &std::path::Path) -> Result<(), Box<dyn Error>> {
    let settings = settings::Settings::load(config)?;
    tracing::info!("Loaded settings from {}", config.display());
    let port = std::env::var("PORT")
        .unwrap_or_else(|_| "7330".into())
        .parse::<u16>()?;
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    // The desktop shell and the development supervisor wait for this exact line.
    #[allow(clippy::print_stdout)]
    {
        println!("ax-devil: http://{}", listener.local_addr()?);
    }
    axum::serve(listener, ax_devil::app(std::sync::Arc::new(settings)))
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
