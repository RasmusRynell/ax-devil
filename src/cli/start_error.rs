//! Launch diagnostics are hidden unless their owner explicitly grants a safe display contract.
use std::{error::Error, net::SocketAddr, path::PathBuf};

#[derive(Debug)]
pub(super) enum StartError {
    Settings(settings::Error),
    Paths(String),
    Launch(String),
    Fixed(&'static str),
    Electron(PathBuf),
    Listen {
        address: SocketAddr,
        kind: std::io::ErrorKind,
    },
    Hidden(Box<dyn Error>),
}

impl<E: Into<Box<dyn Error>>> From<E> for StartError {
    fn from(error: E) -> Self {
        Self::Hidden(error.into())
    }
}

impl StartError {
    /// Only explicitly classified reasons may reach the terminal or shell dialog.
    pub(super) fn shown(&self) -> String {
        match self {
            Self::Settings(error) => error.to_string(),
            Self::Paths(reason) | Self::Launch(reason) => reason.clone(),
            Self::Fixed(reason) => (*reason).to_owned(),
            Self::Electron(path) => ax_devil::desktop::missing_electron(path),
            Self::Listen { address, kind } => format!(
                "Cannot listen on {address}: {kind}. Change PORT or the listener address, or stop the process using it."
            ),
            Self::Hidden(_) => {
                "Application failed to start. Run with --log-level debug for the cause.".to_owned()
            }
        }
    }

    /// Unclassified diagnostics, including their cause chain, are confined to DEBUG logging.
    pub(super) fn detail(&self) -> Option<String> {
        let Self::Hidden(error) = self else {
            return None;
        };
        let mut lines = vec![error.to_string()];
        lines.extend(
            std::iter::successors(error.source(), |cause| (*cause).source())
                .map(|cause| format!("caused by: {cause}")),
        );
        Some(lines.join("\n"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_explicit_sources_have_public_launch_reasons() {
        let cases = [
            (
                StartError::Settings(settings::Error {
                    location: "settings.json".into(),
                    reason: "repair storage.cache_dir".into(),
                    invalid: true,
                    forbidden: false,
                }),
                "settings.json: repair storage.cache_dir",
            ),
            (
                StartError::Paths("Config file does not exist: settings.json".into()),
                "Config file does not exist: settings.json",
            ),
            (
                StartError::Launch("handler: choose a decoder".into()),
                "handler: choose a decoder",
            ),
            (
                StartError::Fixed("This command opens nothing"),
                "This command opens nothing",
            ),
            (
                StartError::Electron(PathBuf::from("missing-electron")),
                "Cannot find the desktop shell's Electron at missing-electron; set AX_DEVIL_DESKTOP to the desktop folder, installed with pnpm install, or AX_DEVIL_ELECTRON to the executable",
            ),
            (
                StartError::Listen {
                    address: "127.0.0.1:7330".parse().unwrap(),
                    kind: std::io::ErrorKind::AddrInUse,
                },
                "Cannot listen on 127.0.0.1:7330: address in use. Change PORT or the listener address, or stop the process using it.",
            ),
            (
                StartError::from("SENTINEL"),
                "Application failed to start. Run with --log-level debug for the cause.",
            ),
        ];
        for (error, expected) in cases {
            assert_eq!(error.shown(), expected);
            assert!(!error.shown().contains("SENTINEL"));
        }
        assert_eq!(
            StartError::from("SENTINEL").detail().as_deref(),
            Some("SENTINEL")
        );
    }
}
