//! `Debug` of [`NewLive`], which holds a stream URL that may carry a password: the URL is printed without it. Every
//! field is named, so a new one cannot be left out unnoticed.

use super::live_stream::NewLive;
use crate::url_without_password;
use std::fmt;

impl fmt::Debug for NewLive {
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
            bare_kept,
        } = self;
        f.debug_struct("NewLive")
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
            .field("bare_kept", bare_kept)
            .finish()
    }
}
