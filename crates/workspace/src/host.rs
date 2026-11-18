//! A camera or broker host as it may be shown.
//!
//! A host that came out of a `$NAME` reference or a resolved stream is whatever the variable held, which can be
//! `user:password@host`. Nothing displays one except through [`DisplayHost`] or [`shown_host`], which hold only a plain
//! host (`foundation::is_plain_host`, the rule connecting uses): labels, names, locations, previews and what the
//! Session tells its owner all go through them.

use crate::credentials::Environment;
use foundation::is_plain_host;
use std::fmt;

/// A resolved or expanded host that is a plain host name or address, and so safe to show.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct DisplayHost(String);

impl DisplayHost {
    /// `value` without surrounding white space, or `None` when it is no plain host.
    pub fn of(value: &str) -> Option<Self> {
        is_plain_host(value).then(|| Self(value.trim().to_owned()))
    }

    /// The host as text.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for DisplayHost {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// What a host text reads as in an environment.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum HostReading {
    /// Text to show: as typed when expanding changes nothing, else a reference's value that is a plain host.
    Host(String),
    /// A reference whose variable is not set.
    Unset,
    /// A reference whose variable is set to something that is no plain host. The value is never kept.
    Rejected,
}

/// What `typed` reads as in `env`.
pub(crate) fn read_host(typed: &str, env: &Environment) -> HostReading {
    let value = env.expand(typed);
    if value == typed {
        HostReading::Host(value)
    } else if value.is_empty() {
        HostReading::Unset
    } else {
        DisplayHost::of(&value).map_or(HostReading::Rejected, |host| HostReading::Host(host.0))
    }
}

/// The text of `typed` as it may be shown in `env`: as typed when expanding changes nothing, a reference's value
/// when it is a plain host, else empty.
pub(crate) fn shown_host(typed: &str, env: &Environment) -> String {
    match read_host(typed, env) {
        HostReading::Host(host) => host,
        HostReading::Unset | HostReading::Rejected => String::new(),
    }
}
