//! Logging for the whole backend: modules log through `tracing` macros and never print.
//!
//! The console (stderr, so stdout stays free for the readiness line) always receives the log; a plain file in the
//! logs folder receives it too when it can be opened.
//!
//! TODO: redact credentials and URL userinfo before they are logged
//! ([03](../../../docs/rebuild/03-runtime.md#logging-must)), and rotate the file.

use std::{fs::OpenOptions, path::Path, str::FromStr, sync::Arc};
use tracing::level_filters::LevelFilter;
use tracing_subscriber::{Layer, layer::SubscriberExt, util::SubscriberInitExt};

/// The verbosity chosen with the global `--log-level` flag.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Level {
    /// Everything, including per-step diagnostics.
    Debug,
    /// Normal operation. The default.
    #[default]
    Info,
    /// Problems the app recovered from.
    Warning,
    /// Failures only.
    Error,
}

impl FromStr for Level {
    type Err = String;

    fn from_str(name: &str) -> Result<Self, String> {
        match name.to_ascii_uppercase().as_str() {
            "DEBUG" => Ok(Self::Debug),
            "INFO" => Ok(Self::Info),
            "WARNING" => Ok(Self::Warning),
            "ERROR" => Ok(Self::Error),
            _ => Err(format!(
                "unknown log level {name:?}; use DEBUG, INFO, WARNING or ERROR"
            )),
        }
    }
}

impl Level {
    fn filter(self) -> LevelFilter {
        match self {
            Self::Debug => LevelFilter::DEBUG,
            Self::Info => LevelFilter::INFO,
            Self::Warning => LevelFilter::WARN,
            Self::Error => LevelFilter::ERROR,
        }
    }
}

/// Starts logging to the console and, when it can be opened, to `file` (its folder is created). Call once at startup.
pub fn init(level: Level, file: Option<&Path>) {
    let console = tracing_subscriber::fmt::layer()
        .with_writer(std::io::stderr)
        .with_filter(level.filter());
    let opened = file.map(|file| {
        file.parent()
            .map_or(Ok(()), std::fs::create_dir_all)
            .and_then(|()| OpenOptions::new().create(true).append(true).open(file))
    });
    let (file_layer, failure) = match opened {
        None => (None, None),
        Some(Ok(handle)) => (
            Some(
                tracing_subscriber::fmt::layer()
                    .with_ansi(false)
                    .with_writer(Arc::new(handle))
                    .with_filter(level.filter()),
            ),
            None,
        ),
        Some(Err(error)) => (None, Some(error)),
    };
    tracing_subscriber::registry()
        .with(console)
        .with(file_layer)
        .init();
    if let (Some(file), Some(error)) = (file, failure) {
        tracing::warn!(
            "Logging to the console only; cannot open {}: {error}",
            file.display()
        );
    }
}
