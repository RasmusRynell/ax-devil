//! Names and credentials: session ids, secrets and join links.
//!
//! A [`SessionId`] only names a session; a [`Secret`] is what admits someone. They are drawn independently, so
//! knowing an id (as the server's password holders do) reveals nothing about the secrets.

use crate::session::Refusal;
use foundation::{hex, random_bytes, random_token};
use std::{fmt, hint::black_box, str::FromStr};

/// Exactly `2 * N` hexadecimal digits, or `Invalid`.
fn read_hex<const N: usize>(text: &str) -> Result<[u8; N], Refusal> {
    if text.len() != 2 * N || !text.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(Refusal::Invalid);
    }
    let mut bytes = [0; N];
    for (byte, pair) in bytes.iter_mut().zip(text.as_bytes().chunks(2)) {
        // Both digits were checked to be ASCII hex above.
        let pair = std::str::from_utf8(pair).map_err(|_| Refusal::Invalid)?;
        *byte = u8::from_str_radix(pair, 16).map_err(|_| Refusal::Invalid)?;
    }
    Ok(bytes)
}

/// Names a session. Random, but not a credential.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct SessionId([u8; 8]);

impl SessionId {
    /// A new random id.
    pub fn generate() -> Self {
        Self(random_bytes())
    }
}

impl fmt::Display for SessionId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&hex(&self.0))
    }
}

impl FromStr for SessionId {
    type Err = Refusal;

    fn from_str(text: &str) -> Result<Self, Refusal> {
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
        Self(random_token())
    }

    /// A secret the operator chose. It travels in an `Authorization: Bearer` header, which a browser can only send
    /// as printable ASCII without spaces (0x21 to 0x7E); anything else is refused with the reason.
    pub fn chosen(text: String) -> Result<Self, &'static str> {
        if text.is_empty() || !text.bytes().all(|byte| (0x21..=0x7E).contains(&byte)) {
            return Err(
                "a password may only use printable ASCII characters without spaces (! to ~), and not be empty",
            );
        }
        Ok(Self(text))
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
    type Err = Refusal;

    fn from_str(text: &str) -> Result<Self, Refusal> {
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
    type Err = Refusal;

    fn from_str(text: &str) -> Result<Self, Refusal> {
        let (session, secret) = text.split_once('.').ok_or(Refusal::Invalid)?;
        Ok(Self {
            session: session.parse()?,
            secret: secret.parse()?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_chosen_password_must_be_sendable_in_a_header() {
        assert!(Secret::chosen("correct-horse_7!".into()).is_ok());
        for refused in ["秘密", "trailing ", " leading", "inner space", "tab\t", ""] {
            assert!(Secret::chosen(refused.into()).is_err(), "{refused:?}");
        }
    }
}
