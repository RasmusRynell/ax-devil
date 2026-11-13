//! A stream URL, read once for everyone who needs it: the app approves and shows it, the camera crate connects to it.

use percent_encoding::percent_decode_str;
pub use url::Url;

/// What a client is shown for a stream URL that cannot be printed without risking its password.
const UNUSABLE_URL: &str = "(unusable URL)";

/// Why a text is not a stream URL.
#[derive(Clone, Copy, Debug)]
pub enum UrlRefusal {
    /// A backslash or control character, which a parser reads as a slash or drops.
    Character,
    /// Not a URL at all.
    Unreadable,
    /// A scheme other than `rtsp`, `http` or `https`, or a URL that is not hierarchical.
    Scheme,
    /// No host.
    NoHost,
}

impl UrlRefusal {
    /// The refusal as a sentence for the user.
    pub fn reason(self) -> &'static str {
        match self {
            Self::Character => "The stream URL has a character that is not allowed.",
            Self::Unreadable => "The stream URL cannot be read.",
            Self::Scheme => "The stream URL is not rtsp://, http:// or https://.",
            Self::NoHost => "The stream URL has no host.",
        }
    }
}

/// Whether `url` is one a stream can be fetched from: `rtsp`, `http` or `https`, hierarchical, with a host.
pub fn check_stream_url(url: &Url) -> Result<(), UrlRefusal> {
    if !matches!(url.scheme(), "rtsp" | "http" | "https") || url.cannot_be_a_base() {
        return Err(UrlRefusal::Scheme);
    }
    if url.host().is_none() {
        return Err(UrlRefusal::NoHost);
    }
    Ok(())
}

/// `text` as the connection reads it: trimmed, parsed by the `url` crate, and failing closed on a backslash or control
/// character (which that parse reads as a slash, or drops) and on what [`check_stream_url`] refuses.
pub fn read_url(text: &str) -> Result<Url, UrlRefusal> {
    let text = text.trim();
    if text.chars().any(|c| c == '\\' || c.is_control()) {
        return Err(UrlRefusal::Character);
    }
    let url = Url::parse(text).map_err(|_| UrlRefusal::Unreadable)?;
    check_stream_url(&url).map(|()| url)
}

/// The percent-decoded user and password of the login `url` carries, or `None` when it has none.
pub fn login(url: &Url) -> Option<(String, String)> {
    let decode = |text: &str| percent_decode_str(text).decode_utf8_lossy().into_owned();
    (!url.username().is_empty() || url.password().is_some()).then(|| {
        (
            decode(url.username()),
            decode(url.password().unwrap_or_default()),
        )
    })
}

/// `url` as a client may be shown it: without the password of its login (`user:pass@` becomes `user@`), and as typed
/// when it is a usable URL with none. Anything else is shown as a placeholder unless it provably holds no login: text
/// that is not a usable URL (opaque, hostless, another scheme, unreadable) or whose cleaned form does not read back
/// as the same URL without a password. This is the one place that decides what may be shown, so a spelling no one
/// thought of can only cost a placeholder.
pub fn url_without_password(url: &str) -> String {
    let text = url.trim();
    let Some(mut cleaned) = Url::parse(text)
        .ok()
        .filter(|url| check_stream_url(url).is_ok())
    else {
        // Text that is no usable URL might still carry a login: an `@`, or a second colon after the scheme's.
        return if text.contains('@') || text.matches(':').count() >= 2 {
            UNUSABLE_URL.to_owned()
        } else {
            text.to_owned()
        };
    };
    if cleaned.password().is_none() {
        return text.to_owned();
    }
    if cleaned.set_password(None).is_err() {
        return UNUSABLE_URL.to_owned();
    }
    let shown = cleaned.to_string();
    match Url::parse(&shown) {
        Ok(back) if back.password().is_none() && back == cleaned => shown,
        _ => UNUSABLE_URL.to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Spellings that hold a password `SECRET` (or its percent-encoded form) in ways a parser reads differently:
    /// opaque, hostless, empty-host, wrong-scheme and unreadable ones among them.
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

    /// The password never survives, whatever way the URL was spelled, and what cannot be cleaned is not shown at all;
    /// also for the text of a URL the camera crate already holds parsed.
    #[test]
    fn no_spelling_of_a_url_shows_its_password() {
        let hidden = |shown: String| {
            let shown = shown.to_ascii_uppercase();
            !shown.contains("SECRET") && !shown.contains("SE%43RET")
        };
        for text in HOSTILE {
            assert!(hidden(url_without_password(text)), "{text}");
            if let Ok(url) = Url::parse(text) {
                assert!(hidden(url_without_password(url.as_str())), "{text}");
            }
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
            assert_eq!(url_without_password(url), "(unusable URL)", "{url}");
        }
    }
}
