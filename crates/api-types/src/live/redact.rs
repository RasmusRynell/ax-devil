//! `Debug` of [`NewLiveWire`], which carries a stream URL that may hold a password: the URL is printed without it.
//! Every field is named, so a new one cannot be left out unnoticed.

use super::NewLiveWire;
use std::fmt;
use workspace::url_without_password;

impl fmt::Debug for NewLiveWire {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let Self {
            label,
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
            kept_mqtt,
            kept_datahub,
            found,
            like,
        } = self;
        f.debug_struct("NewLiveWire")
            .field("label", label)
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
            .field("kept_mqtt", kept_mqtt)
            .field("kept_datahub", kept_datahub)
            .field("found", found)
            .field("like", like)
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use workspace::{LiveOverlay, LiveRecipe, Protocol, RtspRoute, Secret};

    /// Spellings of one URL whose password a plain text search of the URL would not find.
    const SPELLINGS: [&str; 5] = [
        "http://root:SECRET@cam/s",
        "http:root:SECRET@cam/s",
        "http:////root:SECRET@cam/s",
        "HTTP://root:SECRET@CAM:80/s",
        "rtsp://root:SE%43RET@cam/s",
    ];

    fn recipe(url: &str) -> LiveRecipe {
        LiveRecipe {
            host: "cam".into(),
            username: String::new(),
            password: Secret::new(""),
            protocol: Protocol::Https,
            port: 0,
            head: 1,
            rtsp_port: 554,
            rtsp_route: RtspRoute::Direct,
            resolution: None,
            url: Some(url.into()),
            overlay: LiveOverlay::None,
            kept: Default::default(),
        }
    }

    #[test]
    fn a_hostless_or_opaque_url_goes_to_a_client_as_a_placeholder() {
        for url in [
            "rtsp:////root:SECRET@cam/s",
            "rtsp:root:SECRET@cam",
            "rtsp://:SECRET@/s",
        ] {
            let wire = NewLiveWire::from(&recipe(url));
            assert_eq!(wire.url.as_deref(), Some("(unusable URL)"), "{url}");
            assert!(!format!("{wire:?}{wire:#?}").contains("SECRET"), "{url}");
        }
    }

    #[test]
    fn the_request_a_recipe_makes_again_carries_its_urls_user_but_never_its_password() {
        for url in SPELLINGS {
            let wire = NewLiveWire::from(&recipe(url));
            let sent = wire.url.clone().unwrap();
            assert!(sent.contains("root@cam"), "{url} -> {sent}");
            for text in [
                serde_json::to_string(&wire).unwrap(),
                format!("{wire:?}"),
                format!("{wire:#?}"),
            ] {
                assert!(!text.to_uppercase().contains("SECRET"), "{url}: {text}");
            }
        }
    }
}
