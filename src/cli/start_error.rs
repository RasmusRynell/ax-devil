//! Public launch refusals retain their type until the CLI and shell select the same safe report.
use std::{error::Error, fmt};

#[derive(Debug)]
pub(super) enum StartError {
    Settings(settings::Error),
    // Only launch owners construct these reasons: paths, reference names and UI-safe intake refusals, never transport diagnostics.
    Refused(String),
}

impl From<settings::Error> for StartError {
    fn from(error: settings::Error) -> Self {
        Self::Settings(error)
    }
}
impl From<String> for StartError {
    fn from(reason: String) -> Self {
        Self::Refused(reason)
    }
}
impl fmt::Display for StartError {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Settings(error) => error.fmt(out),
            Self::Refused(reason) => out.write_str(reason),
        }
    }
}
impl Error for StartError {}

/// The one safe rendering used by terminal reports, shell forwarding and launch hand-off failures.
pub(super) fn public_reason(error: &(dyn Error + 'static)) -> String {
    if error.is::<StartError>() || error.is::<settings::Error>() {
        error.to_string()
    } else {
        "Application failed to start. Check the configuration and log for details.".to_owned()
    }
}
