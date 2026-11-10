//! The stream URL a camera is connected to, which may carry a password.

use super::url_without_password;
use serde::{Deserialize, Serialize};
use std::{fmt, ops::Deref};

/// A stream URL as typed, with its login if it has one. `Debug` prints it without the password of that login (see
/// [`url_without_password`]), so a type holding one can derive `Debug`. It is the plain text in a message.
#[derive(Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct StreamUrl(String);

impl StreamUrl {
    /// This URL as a client may be shown it: without the password of its login (see [`url_without_password`]).
    pub fn without_password(&self) -> Self {
        Self(url_without_password(&self.0))
    }
}

impl Deref for StreamUrl {
    type Target = str;

    fn deref(&self) -> &str {
        &self.0
    }
}

impl From<String> for StreamUrl {
    fn from(text: String) -> Self {
        Self(text)
    }
}

impl From<&str> for StreamUrl {
    fn from(text: &str) -> Self {
        Self(text.to_owned())
    }
}

impl fmt::Debug for StreamUrl {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(&url_without_password(&self.0), f)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        LiveOverlay, LiveRecipe, Protocol, ResolvedCamera, RtspRoute, Secret, intake::NewLive,
    };

    #[test]
    fn no_debug_of_a_type_holding_a_stream_url_prints_its_password() {
        let url = StreamUrl::from("http:////root:SECRET@cam/s");
        let recipe = LiveRecipe {
            host: "cam".into(),
            username: String::new(),
            password: Secret::new(""),
            protocol: Protocol::Https,
            port: 0,
            head: 1,
            rtsp_port: 554,
            rtsp_route: RtspRoute::Direct,
            resolution: None,
            url: Some(url.clone()),
            overlay: LiveOverlay::None,
            kept: Default::default(),
        };
        let resolved = ResolvedCamera {
            host: "cam".into(),
            protocol: Protocol::Https,
            port: 443,
            rtsp_port: 554,
            rtsp_route: RtspRoute::Direct,
            head: 1,
            resolution: None,
            url: Some(url.clone()),
            login: None,
        };
        let new = NewLive {
            url: Some(url),
            ..NewLive::default()
        };
        for text in [
            format!("{recipe:?}"),
            format!("{recipe:#?}"),
            format!("{resolved:?}"),
            format!("{resolved:#?}"),
            format!("{new:?}"),
            format!("{new:#?}"),
        ] {
            assert!(!text.contains("SECRET"), "{text}");
            assert!(text.contains("http://root@cam/s"), "{text}");
        }
    }
}
