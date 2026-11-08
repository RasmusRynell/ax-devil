//! Where a stream's login goes: the host and port its requests are sent to.

use super::{LiveRecipe, Protocol, RtspRoute, invalid};
use foundation::UserError;
use percent_encoding::percent_decode_str;
use url::{Host, Url};

/// What a client is shown for a stream URL that cannot be printed without risking its password.
const UNUSABLE: &str = "(unusable URL)";

/// A stream URL as the connection reads it: the `url` crate's parse, which the camera crate's `Url::parse` of the same
/// text is too, so a destination approved here is the one connected to. It fails closed: a backslash or control
/// character (which that parse reads as a slash, or drops), a scheme other than `rtsp`, `http` or `https`, and a URL
/// with no host are refused.
fn parsed(text: &str) -> Result<Url, UserError> {
    let field = "url";
    let text = text.trim();
    if text.chars().any(|c| c == '\\' || c.is_control()) {
        return Err(invalid(
            field,
            "The stream URL has a character that is not allowed.",
        ));
    }
    let url = Url::parse(text).map_err(|_| invalid(field, "The stream URL cannot be read."))?;
    if !matches!(url.scheme(), "rtsp" | "http" | "https") || url.cannot_be_a_base() {
        return Err(invalid(
            field,
            "The stream URL is not rtsp://, http:// or https://.",
        ));
    }
    if url.host().is_none() {
        return Err(invalid(field, "The stream URL has no host."));
    }
    Ok(url)
}

/// The host and port a stream URL is connected to, the host lowercase and an IPv6 address in brackets, or why the URL
/// cannot be used. No port is the scheme's standard one: 554 for `rtsp://`, 80 for `http://`, 443 for `https://`. A
/// login in the URL is not part of it. This is the one place a URL's destination is decided.
pub(crate) fn url_endpoint(url: &str) -> Result<(String, u16), UserError> {
    destination(&parsed(url)?).ok_or_else(|| invalid("url", "The stream URL has no host."))
}

/// The host (lowercase, an IPv6 address in brackets) and effective port of a parsed URL: no port is the scheme's
/// standard one, 554 for `rtsp`.
fn destination(url: &Url) -> Option<(String, u16)> {
    let host = match url.host()? {
        Host::Domain(name) => name.to_ascii_lowercase(),
        Host::Ipv4(address) => address.to_string(),
        Host::Ipv6(address) => format!("[{address}]"),
    };
    let standard = if url.scheme() == "rtsp" { 554 } else { 0 };
    let port = url
        .port()
        .or(url.port_or_known_default())
        .unwrap_or(standard);
    Some((host, port))
}

/// Whether the readable stream URL `url` carries a login of its own, which the stream sends there instead of the
/// camera's.
pub(crate) fn url_has_login(url: &str) -> bool {
    parsed(url).is_ok_and(|url| has_login(&url))
}

/// Whether a parsed URL carries a login of its own, which the camera crate then uses instead of the camera's.
fn has_login(url: &Url) -> bool {
    !url.username().is_empty() || url.password().is_some()
}

/// Whether `text`, which is not a clean stream URL, might carry a login: an `@`, or a second colon after the scheme's.
fn credential_like(text: &str) -> bool {
    text.contains('@') || text.matches(':').count() >= 2
}

/// A URL the camera crate can connect to: `rtsp`, `http` or `https`, hierarchical, with a host.
fn usable(url: &Url) -> bool {
    matches!(url.scheme(), "rtsp" | "http" | "https")
        && !url.cannot_be_a_base()
        && url.host_str().is_some_and(|host| !host.is_empty())
}

/// `url` as a client may be shown it: without the password of its login (`user:pass@` becomes `user@`), and as typed
/// when it is a usable URL with none. Anything else is shown as a placeholder unless it provably holds no login: text
/// that is not a usable URL (opaque, hostless, another scheme, unreadable) or whose cleaned form does not read back
/// as the same URL without a password. This is the one place that decides what may be shown, so a spelling no one
/// thought of can only cost a placeholder.
pub fn url_without_password(url: &str) -> String {
    let text = url.trim();
    let Some(parsed) = Url::parse(text).ok().filter(usable) else {
        return if credential_like(text) {
            UNUSABLE.to_owned()
        } else {
            text.to_owned()
        };
    };
    if parsed.password().is_none() {
        return text.to_owned();
    }
    let mut cleaned = parsed;
    if cleaned.set_password(None).is_err() {
        return UNUSABLE.to_owned();
    }
    let shown = cleaned.to_string();
    match Url::parse(&shown) {
        Ok(back) if back.password().is_none() && back == cleaned => shown,
        _ => UNUSABLE.to_owned(),
    }
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
    match (parsed(saved), parsed(sent)) {
        (Ok(saved), Ok(sent)) => {
            saved.password().is_some() && sent.password().is_none() && same_stream(&saved, &sent)
        }
        _ => false,
    }
}

/// Whether two parsed URLs reach the same stream as the same user: scheme, the host as a name (case and a trailing dot
/// aside), the effective port, path, query (byte for byte) and the user as read are equal, and nothing else counts, so `RTSP://root@CAM/s` and
/// `rtsp://root@cam:554/s` are one URL.
fn same_stream(a: &Url, b: &Url) -> bool {
    let identity = |url: &Url| {
        destination(url).map(|(host, port)| {
            (
                url.scheme().to_owned(),
                host.trim_end_matches('.').to_owned(),
                port,
                url.path().to_owned(),
                url.query().map(str::to_owned),
                // The user as the connection reads it, percent-decoded, as the camera crate does.
                percent_decode_str(url.username())
                    .decode_utf8_lossy()
                    .into_owned(),
            )
        })
    };
    identity(a).is_some() && identity(a) == identity(b)
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
        match self.url.as_deref().and_then(|url| parsed(url).ok()) {
            Some(url) if !has_login(&url) => destination(&url).unwrap_or(camera),
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

    /// Spellings that hold a password `SECRET` (or its percent-encoded form) in ways a parser reads differently:
    /// opaque, hostless, empty-host, wrong-scheme and unreadable ones among them. The camera crate's redactor is tested
    /// with the same list, so the two cannot drift apart.
    const HOSTILE: [&str; 24] = [
        "http://root:SECRET@cam/s",
        "http:root:SECRET@cam/s",
        "http:////u:SECRET@h",
        "HTTP://root:SECRET@CAM:80/s",
        "rtsp://root:SE%43RET@cam/s",
        "rtsp://:SECRET@cam",
        "rtsp://root:SECRET@/s",
        "rtsp://user:SECRET@",
        "rtsp:////root:SECRET@cam/s",
        "rtsp:///root:SECRET@cam",
        "rtsp:/root:SECRET@cam/s",
        "RTSP:////u:SECRET@h",
        "rtsp:root:SECRET@cam",
        r"rtsp:\\root:SECRET@cam",
        "mailto:root:SECRET@cam",
        "ftp://root:SECRET@cam/s",
        "rtsps://root:SECRET@cam/s",
        "http://root:SECRET@[::1/s",
        "http://root:SECRET@cam:99999/s",
        "u:SECRET@",
        ":SECRET@",
        "  rtsp://root:SECRET@cam/s  ",
        "rtsp:SECRET:x",
        "not a url:SECRET@x",
    ];

    /// The password never survives, whatever way the URL was spelled, and what cannot be cleaned is not shown at all.
    #[test]
    fn no_spelling_of_a_url_shows_its_password() {
        for url in HOSTILE {
            let shown = url_without_password(url).to_ascii_uppercase();
            assert!(
                !shown.contains("SECRET") && !shown.contains("SE%43RET"),
                "{url} -> {shown}"
            );
        }
    }

    #[test]
    fn a_clean_url_keeps_its_user_and_the_rest_as_it_was() {
        for (url, shown) in [
            ("http://root:secret@cam/s?a=b@c", "http://root@cam/s?a=b@c"),
            ("HTTP://root:secret@CAM:80/s", "http://root@cam/s"),
            ("http:root:secret@cam/s", "http://root@cam/s"),
            ("http:////root:secret@cam/s", "http://root@cam/s"),
            ("http://root:p%40ss:word@cam/s", "http://root@cam/s"),
            ("http://root:pa@ss:wo:rd@cam/s", "http://root@cam/s"),
            ("rtsp://:secret@cam/s", "rtsp://cam/s"),
            ("rtsp://root:a@cam/s", "rtsp://root@cam/s"),
            (
                "https://root:secret@[2001:db8::1]:8443/s",
                "https://root@[2001:db8::1]:8443/s",
            ),
            (r"http://root:secret@cam\s", "http://root@cam/s"),
        ] {
            assert_eq!(url_without_password(url), shown, "{url}");
        }
        // No password: as typed, even when it is not a URL at all.
        for url in [
            "rtsp://cam/s",
            "http://root@cam/s?x=1",
            "rtsp://cam",
            "rtsp://@cam",
            "garbage",
        ] {
            assert_eq!(url_without_password(url), url);
        }
        // Text that might carry a login but is no usable URL is hidden whole.
        for url in [
            "mailto:root:secret@cam",
            "rtsp:////root:secret@cam/s",
            "u:secret@",
        ] {
            assert_eq!(url_without_password(url), UNUSABLE, "{url}");
        }
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
        assert!(url_password_dropped("mailto:root:secret@cam", UNUSABLE));
    }
}
