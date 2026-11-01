//! Errors on the wire. [`project`] is the only place a [`UserError`] becomes wire data, so what an audience may
//! learn from a failure is decided once.

use foundation::{Location, UserError};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Who a wire message is for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Audience {
    /// Whether the audience may see the host's diagnostic text and paths: the reason and the location.
    pub host_details: bool,
}

impl Audience {
    /// The host itself, or an owner who may see its files.
    pub const HOST: Self = Self { host_details: true };
}

/// The public kind of a failure; mirrors [`foundation::ErrorCode`] one to one.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub enum ErrorCode {
    /// The path the user entered names no file.
    InvalidPath,
    /// The file does not exist.
    FileNotFound,
    /// The file or the path cannot be read.
    FileUnreadable,
    /// The file is readable but holds no video.
    NotAVideo,
    /// The video's codec, time base or picture size cannot be used.
    UnsupportedFormat,
    /// Building the frame index failed.
    IndexingFailed,
    /// The file changed after it was indexed or opened.
    FileChanged,
    /// A frame, or the decoder, failed.
    DecodeFailed,
    /// The Content is not in the workspace.
    ContentNotFound,
    /// A client message broke the playback protocol.
    ProtocolViolation,
    /// The video was closed while it was being opened or read.
    VideoClosed,
    /// A fault of the backend itself.
    Internal,
}

impl From<foundation::ErrorCode> for ErrorCode {
    fn from(code: foundation::ErrorCode) -> Self {
        use foundation::ErrorCode as Code;
        match code {
            Code::InvalidPath => Self::InvalidPath,
            Code::FileNotFound => Self::FileNotFound,
            Code::FileUnreadable => Self::FileUnreadable,
            Code::NotAVideo => Self::NotAVideo,
            Code::UnsupportedFormat => Self::UnsupportedFormat,
            Code::IndexingFailed => Self::IndexingFailed,
            Code::FileChanged => Self::FileChanged,
            Code::DecodeFailed => Self::DecodeFailed,
            Code::ContentNotFound => Self::ContentNotFound,
            Code::ProtocolViolation => Self::ProtocolViolation,
            Code::VideoClosed => Self::VideoClosed,
            Code::Internal => Self::Internal,
        }
    }
}

/// The values a client may fill into the message for a code. Only ids, never text.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct ErrorParams {
    /// The workspace item the failure is about.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub item: Option<String>,
}

/// A failure as the wire carries it: the code and its parameters for everyone, and for an audience with host
/// details also the reason and the location.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct ErrorWire {
    /// The public kind.
    pub code: ErrorCode,
    /// The values the code's message needs.
    pub params: ErrorParams,
    /// What went wrong, in words for the user. Only for an audience with host details.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub reason: Option<String>,
    /// What it is about, such as the file path. Only for an audience with host details, and only when it is about
    /// something.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub location: Option<String>,
}

/// The wire form of `error` for `audience`.
pub fn project(error: &UserError, audience: Audience) -> ErrorWire {
    let item = match &error.location {
        Some(Location::Item(item)) => Some(item.to_string()),
        _ => None,
    };
    let (reason, location) = if audience.host_details {
        let location = error.location.as_ref().map(|location| match location {
            Location::Host(path) => path.as_path().display().to_string(),
            Location::Item(item) => item.to_string(),
        });
        (Some(error.reason.clone()), location)
    } else {
        (None, None)
    };
    ErrorWire {
        code: error.code.into(),
        params: ErrorParams { item },
        reason,
        location,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::{ErrorCode as Code, HostPath, ItemId};

    #[test]
    fn only_a_host_audience_learns_the_reason_and_the_location() {
        let reason = "SECRET-REASON rtsp://root:SECRET-PASSWORD@camera".to_string();
        let item = ItemId::generate();
        let at_file = UserError::new(Code::FileUnreadable, reason.clone())
            .at(HostPath::new("/home/SECRET-PATH/gate.mp4"));
        let at_item = UserError::new(Code::Internal, reason).at(item);

        for error in [&at_file, &at_item] {
            let public = serde_json::to_string(&project(
                error,
                Audience {
                    host_details: false,
                },
            ))
            .unwrap();
            for secret in [
                "SECRET-PATH",
                "SECRET-REASON",
                "SECRET-PASSWORD",
                "gate.mp4",
            ] {
                assert!(!public.contains(secret), "{secret} reached {public}");
            }
        }
        let public = project(
            &at_item,
            Audience {
                host_details: false,
            },
        );
        assert_eq!(public.params.item, Some(item.to_string()));
        assert_eq!(public.code, ErrorCode::Internal);

        let host = project(&at_file, Audience::HOST);
        assert_eq!(host.location.as_deref(), Some("/home/SECRET-PATH/gate.mp4"));
        assert!(host.reason.unwrap().contains("SECRET-REASON"));
    }
}
