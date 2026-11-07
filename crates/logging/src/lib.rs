//! Logging for the whole backend: modules log through `tracing` macros and never print.
//!
//! The console (stderr, so stdout stays free for the readiness line) always receives the log; a plain file in the
//! logs folder receives it too when it can be opened. The file is rotated at 25 MiB, keeping 10 backups.
//! Every line carries the source file and line of the event. Dependencies are quieted to warnings, whatever the level.
//!
//! TODO: redact credentials and URL userinfo before they are logged
//! ([03](../../../docs/rebuild/03-runtime.md#logging-must)).

mod rotating;

use rotating::RotatingFile;
use std::{path::Path, str::FromStr, sync::Mutex};
use tracing::level_filters::LevelFilter;
use tracing_subscriber::{
    Layer, filter::Targets, layer::SubscriberExt, registry::LookupSpan, util::SubscriberInitExt,
};

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

    /// The chosen level for the app's own events; dependencies are never more verbose than warnings.
    fn targets(self) -> Targets {
        let dependency = self.filter().min(LevelFilter::WARN);
        NOISY_TARGETS
            .iter()
            .fold(
                Targets::new().with_default(self.filter()),
                |targets, target| targets.with_target(*target, dependency),
            )
            // Targets match by prefix, so `log` would also match this crate; the longer name wins.
            .with_target(module_path!(), self.filter())
    }
}

/// Targets whose events are limited to warnings, as Python limits urllib3 and the other chatty libraries. A target
/// matches its sub-modules, and `hyper` and `tokio` also cover `hyper_util` and the `tokio_*` crates. `log` is where
/// `reqwest` and `tungstenite`, which log through the `log` crate, arrive; the app itself logs through `tracing` only.
const NOISY_TARGETS: &[&str] = &[
    "axum",
    "hyper",
    "log",
    "mio",
    "reqwest",
    "rustls",
    "tokio",
    "tower",
    "tungstenite",
];

/// The size at which the log file is rotated.
const ROTATE_BYTES: u64 = 25 << 20;
/// How many rotated log files are kept besides the live one.
const BACKUPS: usize = 10;

/// The line format shared by the console and the file: every line carries the source file and line of its event.
fn lines<S>() -> tracing_subscriber::fmt::Layer<S>
where
    S: tracing::Subscriber + for<'a> LookupSpan<'a>,
{
    tracing_subscriber::fmt::layer()
        .with_file(true)
        .with_line_number(true)
}

/// Starts logging to the console and, when it can be opened, to `file` (its folder is created). Call once at startup.
pub fn init(level: Level, file: Option<&Path>) {
    let console = lines()
        .with_writer(std::io::stderr)
        .with_filter(level.targets());
    let opened = file.map(|file| {
        file.parent()
            .map_or(Ok(()), std::fs::create_dir_all)
            .and_then(|()| RotatingFile::open(file, ROTATE_BYTES, BACKUPS))
    });
    let (file_layer, failure) = match opened {
        None => (None, None),
        Some(Ok(handle)) => (
            Some(
                lines()
                    .with_ansi(false)
                    .with_writer(Mutex::new(handle))
                    .with_filter(level.targets()),
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    #[derive(Clone, Default)]
    struct Buffer(Arc<Mutex<Vec<u8>>>);

    impl std::io::Write for Buffer {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(bytes);
            Ok(bytes.len())
        }

        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    fn logged(level: Level, emit: impl FnOnce()) -> String {
        let buffer = Buffer::default();
        let writer = buffer.clone();
        let subscriber = tracing_subscriber::registry().with(
            lines()
                .with_ansi(false)
                .with_writer(move || writer.clone())
                .with_filter(level.targets()),
        );
        tracing::subscriber::with_default(subscriber, emit);
        let bytes = buffer.0.lock().unwrap().clone();
        String::from_utf8(bytes).unwrap()
    }

    #[test]
    fn a_line_names_its_source_file_and_line() {
        let out = logged(Level::Info, || tracing::info!("opened"));
        assert!(out.contains("opened"), "{out}");
        assert!(out.contains("crates/logging/src/lib.rs:"), "{out}");
    }

    #[test]
    fn a_dependency_logs_warnings_but_not_debug_even_at_debug() {
        let out = logged(Level::Debug, || {
            tracing::debug!("app-debug");
            tracing::debug!(target: "hyper_util::client", "dependency-debug");
            tracing::info!(target: "log", "dependency-info");
            tracing::warn!(target: "reqwest::connect", "dependency-warning");
        });
        assert!(out.contains("app-debug"), "{out}");
        assert!(out.contains("dependency-warning"), "{out}");
        assert!(!out.contains("dependency-debug"), "{out}");
        assert!(!out.contains("dependency-info"), "{out}");
    }
}
