//! What identifies a camera and how to reach it, as plain values the app builds from its own settings.

use std::fmt;
use std::net::IpAddr;
use std::time::Duration;

use crate::error::{CameraError, ErrorKind};

/// A password. Debug prints `***`; there is no `Display` and no serialization, so it cannot reach a log or a file by
/// accident. Only [`expose`](Secret::expose) reads it, where it is sent.
#[derive(Clone, PartialEq, Eq)]
pub struct Secret(String);

impl Secret {
    /// Wraps `value`.
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    /// The password itself; call it only where it is sent to the camera.
    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("***")
    }
}

/// A user name and password.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Login {
    /// The user name.
    pub user: String,
    /// The password.
    pub password: Secret,
}

/// A host name or IP address, valid inside a URL authority (IPv6 is stored bracketed).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Host(String);

impl Host {
    /// Parses a host name or IP address; a scheme, port, path, user info or space is [`ErrorKind::InvalidTarget`].
    pub fn parse(text: &str) -> Result<Self, CameraError> {
        let text = text.trim();
        let bare = text
            .strip_prefix('[')
            .and_then(|rest| rest.strip_suffix(']'))
            .unwrap_or(text);
        if let Ok(ip) = bare.parse::<IpAddr>() {
            return Ok(Self(match ip {
                IpAddr::V4(v4) => v4.to_string(),
                IpAddr::V6(v6) => format!("[{v6}]"),
            }));
        }
        let name_ok = !text.is_empty()
            && text.len() <= 253
            && text
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.' | b'_'));
        if name_ok {
            Ok(Self(text.to_owned()))
        } else {
            Err(CameraError::new(
                ErrorKind::InvalidTarget,
                "the host is not a host name or IP address",
            ))
        }
    }

    /// The host as written in a URL.
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// The host without the brackets of an IPv6 address, as a socket address wants it.
    pub fn bare(&self) -> &str {
        self.0
            .strip_prefix('[')
            .and_then(|rest| rest.strip_suffix(']'))
            .unwrap_or(&self.0)
    }
}

impl fmt::Display for Host {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// The protocol of the camera's web interface.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scheme {
    /// Plain HTTP.
    Http,
    /// HTTP over TLS.
    Https,
}

impl Scheme {
    /// The scheme as written in a URL.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Http => "http",
            Self::Https => "https",
        }
    }

    /// The standard port.
    pub const fn default_port(self) -> u16 {
        match self {
            Self::Http => 80,
            Self::Https => 443,
        }
    }
}

/// Where the camera's web interface (the device API) is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Web {
    /// Plain or TLS.
    pub scheme: Scheme,
    /// The TCP port.
    pub port: u16,
}

/// An MQTT broker, as the camera and this machine both reach it. Its password is a [`Secret`], so `Debug` hides it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Broker {
    /// Where it is.
    pub host: Host,
    /// The TCP port.
    pub port: u16,
    /// The login, if it needs one.
    pub login: Option<Login>,
}

impl Broker {
    /// A broker at `host` on the standard MQTT port, with no login.
    pub fn new(host: Host) -> Self {
        Self {
            host,
            port: 1883,
            login: None,
        }
    }
}

/// A video channel (camera head) of the device, counted from 1.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Head(u8);

impl Head {
    /// The first channel, which every camera has.
    pub const FIRST: Self = Self(1);

    /// Channel `number` (from 1); `None` for 0.
    pub const fn new(number: u8) -> Option<Self> {
        if number == 0 {
            None
        } else {
            Some(Self(number))
        }
    }

    /// The channel number, from 1.
    pub const fn number(self) -> u8 {
        self.0
    }
}

/// An Axis camera and how to reach it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Camera {
    /// Where it is.
    pub host: Host,
    /// Its web interface.
    pub web: Web,
    /// Its RTSP port.
    pub rtsp_port: u16,
    /// The login, if it needs one.
    pub login: Option<Login>,
    /// How long one request, or one connection attempt, may take.
    pub timeout: Duration,
}

impl Camera {
    /// A camera at `host` on the standard HTTPS and RTSP ports, with no login and a five second timeout. HTTPS is the
    /// default because cameras can turn plain HTTP off.
    pub fn new(host: Host) -> Self {
        Self {
            host,
            web: Web {
                scheme: Scheme::Https,
                port: Scheme::Https.default_port(),
            },
            rtsp_port: 554,
            login: None,
            timeout: Duration::from_secs(5),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secret_and_login_never_print_the_password() {
        let login = Login {
            user: "root".into(),
            password: Secret::new("hunter2-secret"),
        };
        let mut camera = Camera::new(Host::parse("192.0.2.7").unwrap());
        camera.login = Some(login.clone());
        for text in [
            format!("{:?}", login.password),
            format!("{login:?}"),
            format!("{camera:#?}"),
        ] {
            assert!(!text.contains("hunter2"), "{text}");
        }
        assert!(format!("{login:?}").contains("***"));
        assert_eq!(login.password.expose(), "hunter2-secret");
        let mut broker = Broker::new(Host::parse("192.0.2.9").unwrap());
        broker.login = Some(login);
        assert!(!format!("{broker:?}").contains("hunter2"));
    }

    #[test]
    fn host_accepts_names_and_addresses_and_rejects_url_parts() {
        for good in ["cam-1.local", "192.0.2.7", "::1", "[2001:db8::1]"] {
            assert!(Host::parse(good).is_ok(), "{good}");
        }
        assert_eq!(Host::parse("::1").unwrap().as_str(), "[::1]");
        assert_eq!(Host::parse("::1").unwrap().bare(), "::1");
        assert_eq!(Host::parse("192.0.2.7").unwrap().bare(), "192.0.2.7");
        assert_eq!(Host::parse("broker.local").unwrap().bare(), "broker.local");
        for bad in [
            "",
            "http://cam",
            "cam:80",
            "cam/path",
            "user@cam",
            "cam name",
            "cam?x",
        ] {
            assert_eq!(
                Host::parse(bad).unwrap_err().kind,
                ErrorKind::InvalidTarget,
                "{bad:?}"
            );
        }
    }

    #[test]
    fn head_counts_from_one() {
        assert_eq!(Head::new(0), None);
        assert_eq!(Head::new(2).unwrap().number(), 2);
    }
}
