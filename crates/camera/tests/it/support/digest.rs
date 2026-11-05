//! A server-side check of an HTTP Digest `Authorization` header (RFC 7616), independent of the client under test.

use md5::Md5;
use sha2::{Digest, Sha256};

/// The hash a Digest challenge names.
#[derive(Clone, Copy, Debug)]
pub enum Algorithm {
    Md5,
    Sha256,
}

impl Algorithm {
    /// The `algorithm` token of a challenge.
    pub fn token(self) -> &'static str {
        match self {
            Self::Md5 => "MD5",
            Self::Sha256 => "SHA-256",
        }
    }

    fn hash(self, text: &str) -> String {
        let bytes = match self {
            Self::Md5 => Md5::digest(text.as_bytes()).to_vec(),
            Self::Sha256 => Sha256::digest(text.as_bytes()).to_vec(),
        };
        bytes.iter().map(|byte| format!("{byte:02x}")).collect()
    }
}

/// What a client puts in the `response` field for the given challenge and request.
pub struct Exchange<'a> {
    pub algorithm: Algorithm,
    pub user: &'a str,
    pub realm: &'a str,
    pub password: &'a str,
    pub nonce: &'a str,
    pub nc: &'a str,
    pub cnonce: &'a str,
    pub qop: &'a str,
    pub method: &'a str,
    pub uri: &'a str,
}

impl Exchange<'_> {
    /// The expected `response` value.
    pub fn response(&self) -> String {
        let a = self.algorithm;
        let ha1 = a.hash(&format!("{}:{}:{}", self.user, self.realm, self.password));
        let ha2 = a.hash(&format!("{}:{}", self.method, self.uri));
        a.hash(&format!(
            "{ha1}:{}:{}:{}:{}:{ha2}",
            self.nonce, self.nc, self.cnonce, self.qop
        ))
    }
}

/// The `key=value` fields of a `Digest ...` header value.
pub fn fields(header: &str) -> Option<Vec<(String, String)>> {
    let rest = header.strip_prefix("Digest ")?;
    Some(
        rest.split(',')
            .filter_map(|part| part.trim().split_once('='))
            .map(|(key, value)| (key.to_owned(), value.trim_matches('"').to_owned()))
            .collect(),
    )
}

/// Whether `header` answers the challenge (`realm`, `nonce`) for this request with `user` and `password`.
pub fn verify(
    header: &str,
    algorithm: Algorithm,
    (realm, nonce): (&str, &str),
    (user, password): (&str, &str),
    (method, uri): (&str, &str),
) -> bool {
    let Some(fields) = fields(header) else {
        return false;
    };
    let get = |name: &str| {
        fields
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
            .unwrap_or("")
    };
    let expected = Exchange {
        algorithm,
        user,
        realm,
        password,
        nonce,
        nc: get("nc"),
        cnonce: get("cnonce"),
        qop: get("qop"),
        method,
        uri,
    }
    .response();
    get("username") == user
        && get("uri") == uri
        && get("nonce") == nonce
        && get("response") == expected
}
