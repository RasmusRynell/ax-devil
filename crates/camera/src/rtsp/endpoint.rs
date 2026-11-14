//! Where a stream is fetched from: the generated Axis URL or a custom one, parsed once into a [`Target`].

use foundation::{check_stream_url, login};
use url::Url;

use super::{RtspRoute, StreamRequest};
use crate::error::{CameraError, ErrorKind};
use crate::target::{Camera, Login, Scheme, Secret};

/// How the RTSP messages reach the camera.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Transport {
    /// Plain RTSP on the camera's RTSP port.
    Rtsp,
    /// RTSP tunnelled through the camera's web server over HTTP.
    Http,
    /// RTSP tunnelled through the camera's web server over HTTPS.
    Https,
}

/// A stream to fetch: how to reach it, its URL without any login, and the login to answer a challenge with.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Target {
    pub(super) transport: Transport,
    pub(super) url: Url,
    pub(super) login: Option<Login>,
}

impl Target {
    /// The path and query a request line carries.
    pub(super) fn request_target(&self) -> String {
        match self.url.query() {
            Some(query) => format!("{}?{query}", self.url.path()),
            None => self.url.path().to_owned(),
        }
    }
}

/// The target of `request` on `camera`. A custom URL is used as typed; its login, if it has one, wins over the
/// camera's and is taken out of the URL, so no URL held or logged afterwards carries it.
pub(super) fn target(camera: &Camera, request: &StreamRequest) -> Result<Target, CameraError> {
    if request.video.is_none() && !request.metadata {
        return Err(CameraError::new(
            ErrorKind::InvalidTarget,
            "the request asks for neither video nor metadata",
        ));
    }
    match &request.url {
        Some(url) => custom(camera, url),
        None => generated(camera, request),
    }
}

fn generated(camera: &Camera, request: &StreamRequest) -> Result<Target, CameraError> {
    let (transport, scheme, port) = match (request.route, camera.web.scheme) {
        (RtspRoute::Direct, _) => (Transport::Rtsp, "rtsp", camera.rtsp_port),
        (RtspRoute::WebApi, Scheme::Http) => (Transport::Http, "http", camera.web.port),
        (RtspRoute::WebApi, Scheme::Https) => (Transport::Https, "https", camera.web.port),
    };
    let mut url = Url::parse(&format!(
        "{scheme}://{}:{port}/axis-media/media.amp",
        camera.host
    ))
    .map_err(|_| {
        CameraError::new(
            ErrorKind::InvalidTarget,
            "the camera address is not a valid URL",
        )
    })?;
    {
        let mut query = url.query_pairs_mut();
        query.append_pair("camera", &request.head.number().to_string());
        query.append_pair("audio", "0");
        match request.video {
            None => {
                query.append_pair("video", "0");
            }
            Some(size) => {
                if let Some(size) = size {
                    query.append_pair("resolution", &size.to_string());
                }
                // Asks for each frame's capture time in the RTP header extension.
                query.append_pair("onvifreplayext", "1");
            }
        }
        if request.metadata {
            query.append_pair("analytics", "polygon");
        }
    }
    Ok(Target {
        transport,
        url,
        login: camera.login.clone(),
    })
}

/// `url` as a target. The intake accepts any URL text, so what cannot be streamed fails here, for good.
fn custom(camera: &Camera, url: &Url) -> Result<Target, CameraError> {
    if url.scheme() == "rtsps" {
        // Retina speaks plain RTSP only; trying would retry forever as unreachable.
        return Err(CameraError::new(
            ErrorKind::Unsupported,
            "rtsps:// streams are not supported; use an rtsp:// URL",
        ));
    }
    check_stream_url(url).map_err(|_| {
        CameraError::new(
            ErrorKind::InvalidTarget,
            "the stream URL is not an rtsp://, http:// or https:// URL with a host",
        )
    })?;
    let transport = match url.scheme() {
        "rtsp" => Transport::Rtsp,
        "http" => Transport::Http,
        // The check leaves only `https`.
        _ => Transport::Https,
    };
    let mut url = url.clone();
    let login = login(&url).map(|(user, password)| Login {
        user,
        password: Secret::new(password),
    });
    if login.is_some() {
        url.set_username("")
            .and_then(|()| url.set_password(None))
            .map_err(|()| {
                CameraError::new(
                    ErrorKind::InvalidTarget,
                    "the stream URL has no host to log in to",
                )
            })?;
    }
    Ok(Target {
        transport,
        url,
        login: login.or_else(|| camera.login.clone()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::live::Reconnect;
    use crate::target::{Head, Host};
    use crate::vapix::Resolution;

    fn camera() -> Camera {
        let mut camera = Camera::new(Host::parse("192.0.2.7").unwrap());
        camera.login = Some(Login {
            user: "root".into(),
            password: Secret::new("hunter2-secret"),
        });
        camera
    }

    fn request(video: Option<Option<Resolution>>, metadata: bool) -> StreamRequest {
        StreamRequest {
            head: Head::FIRST,
            video,
            metadata,
            route: RtspRoute::Direct,
            url: None,
            reconnect: Reconnect::RTSP,
        }
    }

    fn custom_request(text: &str) -> StreamRequest {
        let mut custom = request(Some(None), false);
        custom.url = Url::parse(text).ok();
        custom
    }

    #[test]
    fn the_url_asks_for_what_is_wanted_and_holds_no_login() {
        let size = Resolution {
            width: 1280,
            height: 720,
        };
        let target = target(&camera(), &request(Some(Some(size)), true)).unwrap();
        assert_eq!(target.transport, Transport::Rtsp);
        assert_eq!(
            target.url.as_str(),
            "rtsp://192.0.2.7:554/axis-media/media.amp?camera=1&audio=0&resolution=1280x720&onvifreplayext=1&analytics=polygon"
        );
        assert_eq!(target.login, camera().login);
        let target = super::target(&camera(), &request(None, true)).unwrap();
        assert_eq!(
            target.url.query(),
            Some("camera=1&audio=0&video=0&analytics=polygon")
        );
        let error = super::target(&camera(), &request(None, false)).unwrap_err();
        assert_eq!(error.kind, ErrorKind::InvalidTarget);
    }

    #[test]
    fn the_web_api_route_uses_the_web_interface() {
        let mut camera = camera();
        let mut request = request(Some(None), false);
        request.route = RtspRoute::WebApi;
        let target = target(&camera, &request).unwrap();
        assert_eq!(target.transport, Transport::Https);
        assert_eq!(target.url.scheme(), "https");
        assert_eq!(target.url.host_str(), Some("192.0.2.7"));
        assert_eq!(target.url.port_or_known_default(), Some(443));
        assert_eq!(
            target.request_target(),
            "/axis-media/media.amp?camera=1&audio=0&onvifreplayext=1"
        );
        camera.web.scheme = Scheme::Http;
        camera.web.port = 8080;
        let target = super::target(&camera, &request).unwrap();
        assert_eq!(target.transport, Transport::Http);
        assert_eq!(target.url.port(), Some(8080));
    }

    #[test]
    fn a_custom_url_is_used_as_typed_and_its_login_is_lifted_out() {
        let target = target(
            &camera(),
            &custom_request("rtsp://192.0.2.8:8554/other?x=1"),
        )
        .unwrap();
        assert_eq!(target.url.as_str(), "rtsp://192.0.2.8:8554/other?x=1");
        assert_eq!(target.login, camera().login, "the camera's login fills in");
        for (text, transport) in [
            ("http://u:p%40ss@192.0.2.8/s?x=1", Transport::Http),
            ("https://u:p%40ss@192.0.2.8/s?x=1", Transport::Https),
            ("rtsp://u:p%40ss@192.0.2.8/s?x=1", Transport::Rtsp),
        ] {
            let target = super::target(&camera(), &custom_request(text)).unwrap();
            assert_eq!(target.transport, transport);
            assert_eq!(target.url.username(), "");
            assert_eq!(target.url.password(), None);
            assert!(!target.url.as_str().contains("p%40ss"), "{text}");
            let login = target.login.expect("the URL's login");
            assert_eq!(login.user, "u");
            assert_eq!(login.password.expose(), "p@ss", "decoded, and it wins");
        }
    }

    #[test]
    fn a_request_never_prints_the_password_of_its_url() {
        let request = custom_request("http://u:hunter2-secret@192.0.2.8/s");
        for text in [format!("{request:?}"), format!("{request:#?}")] {
            assert!(!text.contains("hunter2"), "{text}");
            assert!(text.contains("http://u@192.0.2.8/s"), "{text}");
        }
    }

    #[test]
    fn what_cannot_be_streamed_fails_for_good_without_a_secret_in_the_error() {
        for (bad, kind) in [
            (
                "rtsps://root:hunter2-secret@192.0.2.8/s",
                ErrorKind::Unsupported,
            ),
            (
                "ftp://root:hunter2-secret@192.0.2.8/s",
                ErrorKind::InvalidTarget,
            ),
            (
                "mailto:root:hunter2-secret@192.0.2.8",
                ErrorKind::InvalidTarget,
            ),
        ] {
            let error = target(&camera(), &custom_request(bad)).unwrap_err();
            assert_eq!(error.kind, kind, "{bad}");
            assert!(!error.kind.retryable());
            assert!(!format!("{error:?}").contains("hunter2"));
        }
    }
}
