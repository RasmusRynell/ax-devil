//! Errors on the wire. [`project`] is the only place a [`UserError`] becomes wire data, so what an audience may
//! learn from a failure is decided once.

use crate::live::LiveConfirmation;
use foundation::{ErrorCode, Location, UserError};
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

/// The values a client may fill into the message for a code. Only ids, never text.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct ErrorParams {
    /// The workspace item the failure is about.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub item: Option<String>,
    /// For `NeedsConfirmation`: every reference and host to confirm, each with `POST /api/live/confirm`. Only for an
    /// audience with host details: it names resolved hosts and environment variables.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub confirm: Option<Vec<LiveConfirmation>>,
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
    /// What it is about, such as the file path or the settings field key. Only for an audience with host details,
    /// and only when it is about something.
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
            Location::Field(key) => key.clone(),
        });
        (Some(error.reason.clone()), location)
    } else {
        (None, None)
    };
    ErrorWire {
        code: error.code,
        params: ErrorParams {
            item,
            confirm: (audience.host_details && !error.confirmations.is_empty()).then(|| {
                error
                    .confirmations
                    .iter()
                    .cloned()
                    .map(LiveConfirmation::from)
                    .collect()
            }),
        },
        reason,
        location,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::{HostPath, ItemId};

    #[test]
    fn only_a_host_audience_learns_the_reason_and_the_location() {
        let reason = "SECRET-REASON rtsp://root:SECRET-PASSWORD@camera".to_string();
        let item = ItemId::generate();
        let at_file = UserError::new(ErrorCode::FileUnreadable, reason.clone())
            .at(HostPath::new("/home/SECRET-PATH/gate.mp4"));
        let at_item = UserError::new(ErrorCode::Internal, reason).at(item);

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

    #[test]
    fn only_a_host_audience_learns_what_a_confirmation_is_about() {
        let item = ItemId::generate();
        let mut error =
            UserError::new(ErrorCode::NeedsConfirmation, "sign-in goes to 192.0.2.7").at(item);
        error.confirmations = vec![foundation::Confirmation {
            reference: "SECRET_VAR".into(),
            host: "192.0.2.7".into(),
        }];

        let public = project(
            &error,
            Audience {
                host_details: false,
            },
        );
        let text = serde_json::to_string(&public).unwrap();
        assert!(
            !text.contains("SECRET_VAR") && !text.contains("192.0.2.7"),
            "{text}"
        );
        assert_eq!(public.code, ErrorCode::NeedsConfirmation);
        assert_eq!(public.params.item, Some(item.to_string()));
        assert_eq!(
            project(&error, Audience::HOST)
                .params
                .confirm
                .unwrap()
                .len(),
            1
        );
    }
}
