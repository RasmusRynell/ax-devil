//! `Debug` of the types that hold a stream URL, which may carry a password: the URL is printed without it. Every field
//! is named, so a new one cannot be left out unnoticed.

use super::{LiveRecipe, ResolvedCamera, url_without_password};
use std::fmt;

impl fmt::Debug for LiveRecipe {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let Self {
            host,
            username,
            password,
            protocol,
            port,
            head,
            rtsp_port,
            rtsp_route,
            resolution,
            url,
            overlay,
            kept,
        } = self;
        f.debug_struct("LiveRecipe")
            .field("host", host)
            .field("username", username)
            .field("password", password)
            .field("protocol", protocol)
            .field("port", port)
            .field("head", head)
            .field("rtsp_port", rtsp_port)
            .field("rtsp_route", rtsp_route)
            .field("resolution", resolution)
            .field("url", &url.as_deref().map(url_without_password))
            .field("overlay", overlay)
            .field("kept", kept)
            .finish()
    }
}

impl fmt::Debug for ResolvedCamera {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let Self {
            host,
            protocol,
            port,
            rtsp_port,
            rtsp_route,
            head,
            resolution,
            url,
            login,
        } = self;
        f.debug_struct("ResolvedCamera")
            .field("host", host)
            .field("protocol", protocol)
            .field("port", port)
            .field("rtsp_port", rtsp_port)
            .field("rtsp_route", rtsp_route)
            .field("head", head)
            .field("resolution", resolution)
            .field("url", &url.as_deref().map(url_without_password))
            .field("login", login)
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{LiveOverlay, Protocol, RtspRoute, Secret, intake::NewLive};

    const URL: &str = "http:////root:SECRET@cam/s";

    #[test]
    fn no_debug_of_a_type_holding_a_stream_url_prints_its_password() {
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
            url: Some(URL.into()),
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
            url: Some(URL.into()),
            login: None,
        };
        let new = NewLive {
            url: Some(URL.into()),
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
