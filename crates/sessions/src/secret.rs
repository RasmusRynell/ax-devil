//! Names and credentials: session ids, secrets and join links.
//!
//! A [`SessionId`] only names a session; a [`Secret`] is what admits someone. They are drawn independently, so
//! knowing an id (as the server's password holders do) reveals nothing about the secrets.

use crate::SessionError;
use std::{
    fmt::{self, Write as _},
    hint::black_box,
    str::FromStr,
};

fn random<const N: usize>() -> [u8; N] {
    let mut bytes = [0; N];
    // Without operating-system randomness there is no sound way to make an unguessable secret; stopping is safer
    // than falling back to something guessable.
    getrandom::fill(&mut bytes)
        .unwrap_or_else(|error| panic!("the operating system has no randomness: {error}"));
    bytes
}

fn write_hex(bytes: &[u8], f: &mut fmt::Formatter<'_>) -> fmt::Result {
    bytes.iter().try_for_each(|byte| write!(f, "{byte:02x}"))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().fold(String::new(), |mut text, byte| {
        // Writing to a String cannot fail.
        let _ = write!(text, "{byte:02x}");
        text
    })
}

/// Exactly `2 * N` hexadecimal digits, or `Malformed`.
fn read_hex<const N: usize>(text: &str) -> Result<[u8; N], SessionError> {
    if text.len() != 2 * N || !text.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(SessionError::Malformed);
    }
    let mut bytes = [0; N];
    for (byte, pair) in bytes.iter_mut().zip(text.as_bytes().chunks(2)) {
        // Both digits were checked to be ASCII hex above.
        let pair = std::str::from_utf8(pair).map_err(|_| SessionError::Malformed)?;
        *byte = u8::from_str_radix(pair, 16).map_err(|_| SessionError::Malformed)?;
    }
    Ok(bytes)
}

/// Names a session. Random, but not a credential.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct SessionId([u8; 8]);

impl SessionId {
    pub(crate) fn generate() -> Self {
        Self(random())
    }
}

impl fmt::Display for SessionId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write_hex(&self.0, f)
    }
}

impl FromStr for SessionId {
    type Err = SessionError;

    fn from_str(text: &str) -> Result<Self, SessionError> {
        read_hex(text).map(Self)
    }
}

/// A bearer secret: what a link, a resume token or the server password is made of. Generated ones are 128 random
/// bits shown as 32 hex digits; the operator may choose the server password instead. Compared in constant time and
/// never printed by `Debug`.
#[derive(Clone)]
pub struct Secret(String);

impl Secret {
    /// A new secret from the operating system's randomness.
    pub fn generate() -> Self {
        Self(hex(&random::<16>()))
    }

    /// A secret the operator chose, which can be any text.
    pub fn chosen(text: String) -> Self {
        Self(text)
    }

    /// Compares without stopping at the first difference, so timing says nothing about how much matched. The length
    /// is not hidden.
    pub fn matches(&self, candidate: &str) -> bool {
        let (expected, candidate) = (self.0.as_bytes(), candidate.as_bytes());
        let difference = expected
            .iter()
            .zip(candidate)
            .fold(0u8, |difference, (a, b)| difference | (a ^ b));
        expected.len() == candidate.len() && black_box(difference) == 0
    }
}

impl PartialEq for Secret {
    fn eq(&self, other: &Self) -> bool {
        self.matches(&other.0)
    }
}

impl Eq for Secret {}

impl fmt::Debug for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Secret(..)")
    }
}

impl fmt::Display for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// Reads the 32 hex digits a generated secret is shown as; the operator's own password has no parsed form.
impl FromStr for Secret {
    type Err = SessionError;

    fn from_str(text: &str) -> Result<Self, SessionError> {
        read_hex::<16>(text).map(|bytes| Self(hex(&bytes)))
    }
}

/// Admits its holder to one session: `<session id>.<secret>`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct JoinLink {
    /// The session the link opens.
    pub session: SessionId,
    /// What proves the holder was given the link.
    pub secret: Secret,
}

impl fmt::Display for JoinLink {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}.{}", self.session, self.secret)
    }
}

impl FromStr for JoinLink {
    type Err = SessionError;

    fn from_str(text: &str) -> Result<Self, SessionError> {
        let (session, secret) = text.split_once('.').ok_or(SessionError::Malformed)?;
        Ok(Self {
            session: session.parse()?,
            secret: secret.parse()?,
        })
    }
}
