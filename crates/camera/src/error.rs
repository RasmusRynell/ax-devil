//! Errors typed at the source: a caller decides whether to retry from the kind, never from text.

use std::fmt;

/// Why an MQTT broker refused a connection (the refusal codes of the protocol, no text).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Refusal {
    /// The broker does not speak the protocol version offered.
    UnsupportedProtocol,
    /// The broker rejected the client identifier.
    ClientIdRejected,
    /// The broker rejected the user name or password.
    BadCredentials,
    /// The broker does not authorise this client.
    NotAuthorized,
}

/// What went wrong. Where a retry cannot help, [`retryable`](ErrorKind::retryable) is false.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorKind {
    /// The camera wants a login and none was given.
    LoginRequired,
    /// The camera turned the login away.
    LoginRejected,
    /// The login is accepted but not allowed to do this.
    Forbidden,
    /// The device answered, but not as an Axis camera.
    NotAxis,
    /// The device has no such resource.
    NotFound,
    /// The device cannot do this (an authentication scheme, an API, a request).
    Unsupported,
    /// The camera does not offer the analytics data source asked for.
    SourceUnavailable,
    /// The MQTT broker refused the connection.
    BrokerRefused(Refusal),
    /// Something in this process already holds what this needs: a camera has one MQTT client, and another
    /// subscription is using it.
    Busy,
    /// The camera holds something this crate could not put back after using it (an MQTT client password the camera
    /// does not reveal), so it was not touched.
    Unrestorable,
    /// The target is not usable as given: a malformed host, or an answer from elsewhere (a redirect).
    InvalidTarget,
    /// No connection could be made.
    Unreachable,
    /// The device did not answer in time.
    TimedOut,
    /// An established connection ended.
    Lost,
    /// A live stream stopped delivering.
    Silent,
    /// The device sent something that does not parse.
    Malformed,
    /// The device is turning requests away for now (HTTP 429).
    Throttled,
    /// The device failed.
    DeviceFault,
}

impl ErrorKind {
    /// Whether trying again can succeed. The one place this is decided.
    pub const fn retryable(self) -> bool {
        match self {
            Self::Unreachable
            | Self::TimedOut
            | Self::Lost
            | Self::Silent
            | Self::Malformed
            | Self::Throttled
            | Self::DeviceFault => true,
            Self::LoginRequired
            | Self::LoginRejected
            | Self::Forbidden
            | Self::NotAxis
            | Self::NotFound
            | Self::Unsupported
            | Self::SourceUnavailable
            | Self::BrokerRefused(_)
            | Self::Busy
            | Self::Unrestorable
            | Self::InvalidTarget => false,
        }
    }

    /// The kind of a failure status other than 401 (which the caller types, knowing whether a login was sent).
    pub(crate) const fn of_status(status: u16) -> Self {
        match status {
            300..=399 => Self::InvalidTarget,
            403 => Self::Forbidden,
            404 | 410 => Self::NotFound,
            408 => Self::TimedOut,
            429 => Self::Throttled,
            500..=599 => Self::DeviceFault,
            _ => Self::Unsupported,
        }
    }

    /// A short reason a person can read.
    pub const fn reason(self) -> &'static str {
        match self {
            Self::LoginRequired => "the camera requires a login",
            Self::LoginRejected => "the camera rejected the login",
            Self::Forbidden => "the login is not allowed to do this",
            Self::NotAxis => "the device is not an Axis camera",
            Self::NotFound => "the camera does not have this",
            Self::Unsupported => "the camera does not support this",
            Self::SourceUnavailable => "the camera does not offer this analytics data source",
            Self::BrokerRefused(_) => "the MQTT broker refused the connection",
            Self::Busy => "the camera's MQTT client is in use",
            Self::Unrestorable => "the camera's MQTT client settings cannot be restored afterwards",
            Self::InvalidTarget => "the camera address cannot be used",
            Self::Unreachable => "the camera cannot be reached",
            Self::TimedOut => "the camera did not answer in time",
            Self::Lost => "the connection to the camera was lost",
            Self::Silent => "the camera stopped sending",
            Self::Malformed => "the camera sent something unreadable",
            Self::Throttled => "the camera is turning requests away for now",
            Self::DeviceFault => "the camera reported a fault",
        }
    }
}

/// A failure talking to a camera: a [`kind`](CameraError::kind) to act on and a detail to show.
///
/// The detail is built from statuses and transport errors only; it never contains a login or a password.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CameraError {
    /// What went wrong.
    pub kind: ErrorKind,
    detail: String,
    /// Bounded MQTT alternatives and count omitted; host-side detail only.
    pub source_alternatives: Option<(Vec<String>, usize)>,
}

impl CameraError {
    /// An error of `kind`; `detail` must not contain a secret. Anything unprintable in it is escaped, as it may hold
    /// text the camera supplied and reaches logs and terminals.
    pub(crate) fn new(kind: ErrorKind, detail: impl Into<String>) -> Self {
        Self {
            kind,
            source_alternatives: None,
            detail: crate::logtext::escaped(&detail.into()),
        }
    }

    /// An error of `kind` for a failed socket operation, told by the error's kind (`connection refused`, `broken
    /// pipe`: constant words) and never by its text, which can quote what was being connected to.
    pub(crate) fn io(kind: ErrorKind, error: &std::io::Error) -> Self {
        Self::new(kind, error.kind().to_string())
    }

    /// An error of `kind`, for the app's tests to stand in for a camera's (feature `fake`). `detail` must not contain a
    /// secret.
    #[cfg(feature = "fake")]
    pub fn fake(kind: ErrorKind, detail: impl Into<String>) -> Self {
        Self::new(kind, detail)
    }

    /// What happened, for the log or the user.
    pub fn detail(&self) -> &str {
        &self.detail
    }
}

impl fmt::Display for CameraError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.detail.is_empty() {
            f.write_str(self.kind.reason())
        } else {
            write!(f, "{}: {}", self.kind.reason(), self.detail)
        }
    }
}

impl std::error::Error for CameraError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_transient_kinds_retry() {
        let table = [
            (ErrorKind::LoginRequired, false),
            (ErrorKind::LoginRejected, false),
            (ErrorKind::Forbidden, false),
            (ErrorKind::NotAxis, false),
            (ErrorKind::NotFound, false),
            (ErrorKind::Unsupported, false),
            (ErrorKind::SourceUnavailable, false),
            (ErrorKind::BrokerRefused(Refusal::BadCredentials), false),
            (ErrorKind::Busy, false),
            (ErrorKind::Unrestorable, false),
            (ErrorKind::InvalidTarget, false),
            (ErrorKind::Unreachable, true),
            (ErrorKind::TimedOut, true),
            (ErrorKind::Lost, true),
            (ErrorKind::Silent, true),
            (ErrorKind::Malformed, true),
            (ErrorKind::Throttled, true),
            (ErrorKind::DeviceFault, true),
        ];
        for (kind, retryable) in table {
            assert_eq!(kind.retryable(), retryable, "{kind:?}");
        }
    }

    #[test]
    fn statuses_map_to_kinds() {
        let table = [
            (301, ErrorKind::InvalidTarget),
            (308, ErrorKind::InvalidTarget),
            (403, ErrorKind::Forbidden),
            (404, ErrorKind::NotFound),
            (408, ErrorKind::TimedOut),
            (429, ErrorKind::Throttled),
            (500, ErrorKind::DeviceFault),
            (503, ErrorKind::DeviceFault),
            (400, ErrorKind::Unsupported),
            (405, ErrorKind::Unsupported),
        ];
        for (code, kind) in table {
            assert_eq!(ErrorKind::of_status(code), kind, "{code}");
        }
    }

    #[test]
    fn display_joins_reason_and_detail() {
        let error = CameraError::new(ErrorKind::TimedOut, "after 5 s");
        assert_eq!(
            error.to_string(),
            "the camera did not answer in time: after 5 s"
        );
        assert_eq!(
            CameraError::new(ErrorKind::Lost, "").to_string(),
            "the connection to the camera was lost"
        );
    }
}
