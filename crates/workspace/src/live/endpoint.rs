//! Where a stream's login goes: the host and port its requests are sent to.

use super::{LiveRecipe, Protocol, RtspRoute, invalid};
pub use foundation::url_without_password;
use foundation::{Url, UserError, login, read_url};

/// The host and port a stream URL is connected to, or why the URL cannot be used. This is the one place a URL's
/// destination is decided.
pub(crate) fn url_endpoint(url: &str) -> Result<(String, u16), UserError> {
    read_url(url)
        .map(|url| destination(&url))
        .map_err(|refusal| invalid("url", refusal.reason()))
}

/// The host (lowercase, an IPv6 address in brackets) and effective port of a stream URL: no port is the scheme's standard
/// one, 554 for `rtsp`. A login in the URL is not part of it.
fn destination(url: &Url) -> (String, u16) {
    let host = url.host_str().unwrap_or_default().to_ascii_lowercase();
    (host, url.port_or_known_default().unwrap_or(554))
}

/// Whether the readable stream URL `url` carries a login of its own, which the stream sends there instead of the
/// camera's.
pub(crate) fn url_has_login(url: &str) -> bool {
    read_url(url).is_ok_and(|url| login(&url).is_some())
}

/// Whether `sent`, a URL a client sends back, is `saved` as the client was shown it: the same URL, minus the password
/// that `saved` has and the client was never sent. Someone who may restore the password puts it back; no one else may
/// save the URL in that form.
pub fn url_password_dropped(saved: &str, sent: &str) -> bool {
    let shown = url_without_password(saved);
    if shown == saved.trim() {
        return false;
    }
    if sent.trim() == shown {
        return true;
    }
    match (read_url(saved), read_url(sent)) {
        (Ok(saved), Ok(sent)) => {
            let password = |url: &Url| login(url).unwrap_or_default().1;
            !password(&saved).is_empty() && password(&sent).is_empty() && same_stream(&saved, &sent)
        }
        _ => false,
    }
}

/// Whether two URLs reach the same stream as the same user: scheme, the host as a name (case and a trailing dot
/// aside), the effective port, path, query (byte for byte) and the user as read are equal, and nothing else counts, so
/// `RTSP://root@CAM/s` and `rtsp://root@cam:554/s` are one URL.
fn same_stream(a: &Url, b: &Url) -> bool {
    let identity = |url: &Url| {
        let (host, port) = destination(url);
        (
            url.scheme().to_owned(),
            host.trim_end_matches('.').to_owned(),
            port,
            url.path().to_owned(),
            url.query().map(str::to_owned),
            login(url).unwrap_or_default().0,
        )
    };
    identity(a) == identity(b)
}

/// The host and port the camera itself takes a stream on, whatever its URL: the RTSP port, or for the web API route the
/// web interface's (`web_port` 0 is the protocol's standard one). The host is trimmed and lowercase.
pub(crate) fn camera_endpoint(
    host: &str,
    route: RtspRoute,
    protocol: Protocol,
    web_port: u16,
    rtsp_port: u16,
) -> (String, u16) {
    let port = match (route, web_port) {
        (RtspRoute::Direct, _) => rtsp_port,
        (RtspRoute::WebApi, 0) => protocol.default_port(),
        (RtspRoute::WebApi, port) => port,
    };
    (host.trim().to_ascii_lowercase(), port)
}

impl LiveRecipe {
    /// The host and port the camera login of this stream is trusted at: the stream URL's when the URL is readable and
    /// has no login of its own, since the login then goes there; otherwise the camera's, as the URL's own login is
    /// what that URL gets, and a URL that cannot be read gets nothing, so the camera's login has not been sent to
    /// its host.
    pub(crate) fn rtsp_endpoint(&self) -> (String, u16) {
        let camera = camera_endpoint(
            &self.host,
            self.rtsp_route,
            self.protocol,
            self.port,
            self.rtsp_port,
        );
        match self.url.as_deref().map(read_url) {
            Some(Ok(url)) if login(&url).is_none() => destination(&url),
            _ => camera,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn endpoint(url: &str) -> Option<(String, u16)> {
        url_endpoint(url).ok()
    }

    fn at(host: &str, port: u16) -> Option<(String, u16)> {
        Some((host.to_owned(), port))
    }

    /// Every spelling that connects to `cam` on port 554 is that destination, and every one that the connection reads
    /// as another host, or that cannot be read as one thing, is not.
    #[test]
    fn a_stream_urls_destination_is_what_the_connection_reads_whatever_the_spelling() {
        for url in [
            "rtsp://cam/s",
            "RTSP://CAM:554/s",
            "http://cam:554/s",
            "HTTP://Cam:554/s",
            "http:root:secret@cam:554/s",
            "http:////root:secret@cam:554/s",
            "http://root:p%40ss%3Aword@cam:554/s",
            "http://a@b:c@cam:554/s",
            "http://root:pa@ss:wo:rd@cam:554/s?x=@evil:1",
            " http://root@cam:554/s ",
        ] {
            assert_eq!(endpoint(url), at("cam", 554), "{url}");
        }
        for url in [
            r"http://attacker:8080\@cam:554/s",
            r"http://attacker:8080\\@cam:554/s",
            "http://attacker:8080/@cam:554/s",
            "http://attacker:8080#@cam:554/s",
            "http://attacker:8080?@cam:554/s",
            "http://attacker:8080@/cam:554/s",
            "http://root@attacker:8080/s",
            "http://ca\tm:554/s",
            "http://ca\nm:554/s",
            "rtsps://cam/s",
            "ftp://cam/s",
            "mailto:root:secret@cam",
            "cam:554/s",
            "http://",
            "http:///s",
            "http://cam:99999/s",
            "http://cam:x/s",
        ] {
            assert_ne!(endpoint(url), at("cam", 554), "{url}");
        }
        // The backslash that the connection would read as a slash is refused outright, not read as one.
        assert_eq!(endpoint(r"http://attacker:8080\@cam:554/s"), None);
        assert_eq!(
            endpoint("http://attacker:8080/@cam:554/s"),
            at("attacker", 8080)
        );
    }

    #[test]
    fn default_ports_ipv6_literals_and_case_are_one_destination() {
        assert_eq!(endpoint("http://cam/s"), at("cam", 80));
        assert_eq!(endpoint("https://CAM/s"), at("cam", 443));
        assert_eq!(endpoint("HTTPS://cam:443/s"), at("cam", 443));
        assert_eq!(endpoint("http://cam:443/s"), at("cam", 443));
        assert_eq!(endpoint("rtsp://cam/s"), at("cam", 554));
        assert_eq!(endpoint("rtsp://cam:8554/s"), at("cam", 8554));
        assert_eq!(
            endpoint("https://u@[2001:DB8:0::1]:8443/s"),
            at("[2001:db8::1]", 8443)
        );
        assert_eq!(endpoint("rtsp://[2001:db8::1]/s"), at("[2001:db8::1]", 554));
        assert_eq!(endpoint("http://192.0.2.007/s"), at("192.0.2.7", 80));
    }

    #[test]
    fn only_the_url_as_shown_comes_back_as_the_saved_one_with_its_password_dropped() {
        let saved = "http:root:secret@cam/s?x=1";
        for sent in [
            "http://root@cam/s?x=1",
            "HTTP://root@CAM:80/s?x=1",
            " http://root@cam/s?x=1 ",
        ] {
            assert!(url_password_dropped(saved, sent), "{sent}");
        }
        for sent in [
            "http://root:secret@cam/s?x=1",
            "http://root@other/s?x=1",
            "http://cam/s?x=1",
            "http://root@cam/s?x=2",
        ] {
            assert!(!url_password_dropped(saved, sent), "{sent}");
        }
        // `rtsp` keeps the case of a host and an explicit default port, which the connection does not tell apart.
        let saved = "rtsp://root:secret@cam/s?x=1";
        for sent in [
            "rtsp://root@cam/s?x=1",
            "RTSP://root@CAM/s?x=1",
            "rtsp://root@cam:554/s?x=1",
            "rtsp://root@Cam.:554/s?x=1#frag",
        ] {
            assert!(url_password_dropped(saved, sent), "{sent}");
        }
        // The user is compared as the connection reads it, percent-decoded; the path and query byte for byte.
        let saved = "rtsp://r%6Fot:secret@cam/s%2Fa?x=%41";
        for sent in [
            "rtsp://root@cam/s%2Fa?x=%41",
            "rtsp://r%6fot@cam/s%2Fa?x=%41",
            "rtsp://r%6Fot@CAM:554/s%2Fa?x=%41",
        ] {
            assert!(url_password_dropped(saved, sent), "{sent}");
        }
        for sent in [
            "rtsp://root@cam/s/a?x=%41",
            "rtsp://root@cam/s%2Fa?x=A",
            "rtsp://r%6Fo@cam/s%2Fa?x=%41",
        ] {
            assert!(!url_password_dropped(saved, sent), "{sent}");
        }
        for sent in [
            "rtsp://root@cam:8554/s?x=1",
            "rtsp://root@cam2/s?x=1",
            "rtsp://other@cam/s?x=1",
            "rtsp://root@cam/s/?x=1",
            "http://root@cam:554/s?x=1",
        ] {
            assert!(!url_password_dropped(saved, sent), "{sent}");
        }
        // A saved URL with no password hides nothing, and an unreadable one comes back as its placeholder.
        assert!(!url_password_dropped(
            "http://root@cam/s",
            "http://root@cam/s"
        ));
        assert!(url_password_dropped(
            "mailto:root:secret@cam",
            "(unusable URL)"
        ));
    }
}
