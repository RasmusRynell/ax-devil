//! What a camera or broker host may look like.

use std::net::IpAddr;

/// Whether `text` is a plain host name or IP address (an IPv6 one bracketed or not), valid inside a URL authority: no
/// scheme, port, path, user info or space. Surrounding white space is ignored. The rule for showing a value that came
/// out of a `$NAME` reference or a resolved stream, which may carry a login, and what `camera::Host::parse` accepts.
pub fn is_plain_host(text: &str) -> bool {
    let text = text.trim();
    let bare = text
        .strip_prefix('[')
        .and_then(|rest| rest.strip_suffix(']'))
        .unwrap_or(text);
    bare.parse::<IpAddr>().is_ok()
        || (!text.is_empty()
            && text.len() <= 253
            && text
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.' | b'_')))
}

#[cfg(test)]
mod tests {
    use super::is_plain_host;

    #[test]
    fn only_a_bare_name_or_address_is_a_plain_host() {
        for plain in ["cam.local", "192.0.2.7", "[::1]", "::1", " cam-1 "] {
            assert!(is_plain_host(plain), "{plain}");
        }
        for other in [
            "",
            "user:pw@cam.local",
            "cam.local/path",
            "rtsp://cam.local",
            "cam.local:554",
            "cam local",
            "cam\nlocal",
        ] {
            assert!(!is_plain_host(other), "{other:?}");
        }
    }
}
