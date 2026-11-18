//! The `Authorization` header for a camera's 401 challenge: Digest, or Basic only where it is allowed.

use http_auth::{PasswordClient, PasswordParams};

use crate::error::{CameraError, ErrorKind};
use crate::target::Login;

/// What a request must say so the camera can check the login: the request line and, when it has one, its body.
pub(crate) struct Signed<'a> {
    pub(crate) method: &'a str,
    /// The request target, path and query.
    pub(crate) uri: &'a str,
    /// The body the signature covers (Digest `auth-int`), or `None` when it is not available to sign, as a request
    /// whose body streams: only `auth` can then be answered.
    pub(crate) body: Option<&'a [u8]>,
}

/// The header value answering `challenges` (the `WWW-Authenticate` values of a 401) for `login`.
///
/// Digest is used whenever it is offered. Basic is used only when it is the only usable scheme and `basic_allowed`
/// (an HTTPS connection), so a password is never sent in the clear.
pub(crate) fn authorization(
    challenges: &[&str],
    login: &Login,
    basic_allowed: bool,
    request: &Signed<'_>,
) -> Result<String, CameraError> {
    Responder::new(challenges, login, basic_allowed)?.answer(request)
}

/// One challenge answered more than once: a Digest answer carries the next nonce count each time, so the requests of
/// one connection sequence (the tunnel's GET, then its POST) can all be signed from the one challenge.
pub(crate) struct Responder<'a> {
    client: PasswordClient,
    login: &'a Login,
}

impl<'a> Responder<'a> {
    /// Chooses the scheme as [`authorization`] does.
    pub(crate) fn new(
        challenges: &[&str],
        login: &'a Login,
        basic_allowed: bool,
    ) -> Result<Self, CameraError> {
        let mut builder = PasswordClient::builder();
        for challenge in challenges {
            builder = builder.challenges(challenge);
        }
        let client = builder.build().map_err(|_| {
            CameraError::new(
                ErrorKind::Unsupported,
                "the camera offers no authentication scheme we support",
            )
        })?;
        if matches!(client, PasswordClient::Basic(_)) && !basic_allowed {
            return Err(CameraError::new(
                ErrorKind::Unsupported,
                "the camera offers only Basic authentication, which is not sent here",
            ));
        }
        Ok(Self { client, login })
    }

    /// The header value for `request`.
    pub(crate) fn answer(&mut self, request: &Signed<'_>) -> Result<String, CameraError> {
        self.client
            .respond(&PasswordParams {
                username: &self.login.user,
                password: self.login.password.expose(),
                uri: request.uri,
                method: request.method,
                body: request.body,
            })
            .map_err(|_| match request.body {
                // Nothing was missing but the body: the camera insists on signing it (`auth-int` alone).
                None => CameraError::new(
                    ErrorKind::Unsupported,
                    "the camera wants the request body signed, which a stream cannot be",
                ),
                Some(_) => CameraError::new(
                    ErrorKind::Malformed,
                    "the authentication challenge cannot be answered",
                ),
            })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::target::Secret;

    fn login() -> Login {
        Login {
            user: "Mufasa".into(),
            password: Secret::new("Circle of Life"),
        }
    }

    fn request() -> Signed<'static> {
        Signed {
            method: "GET",
            uri: "/dir/index.html",
            body: Some(b""),
        }
    }

    // The challenge of RFC 7616 section 3.9.1.
    const RFC7616: &str = r#"Digest realm="http-auth@example.org", qop="auth, auth-int", algorithm=SHA-256, nonce="7ypf/xlj9XXwfDPEoM4URrv/xwf94BcCAzFZH4GiTo0v", opaque="FQhe/qaU925kfnzjCev0ciny7QMkPqMAFRtzCUYo5tdS""#;

    #[test]
    fn digest_answers_the_rfc_7616_challenge() {
        let header = authorization(&[RFC7616], &login(), false, &request()).unwrap();
        for part in [
            "Digest ",
            r#"username="Mufasa""#,
            r#"realm="http-auth@example.org""#,
            "algorithm=SHA-256",
            r#"uri="/dir/index.html""#,
            r#"opaque="FQhe/qaU925kfnzjCev0ciny7QMkPqMAFRtzCUYo5tdS""#,
        ] {
            assert!(header.contains(part), "{part} missing from {header}");
        }
        assert!(
            !header.contains("Circle"),
            "the password is not in the header"
        );
    }

    #[test]
    fn basic_only_over_https_and_only_when_alone() {
        let basic = r#"Basic realm="cam""#;
        let refused = authorization(&[basic], &login(), false, &request()).unwrap_err();
        assert_eq!(refused.kind, ErrorKind::Unsupported);
        let header = authorization(&[basic], &login(), true, &request()).unwrap();
        assert_eq!(header, "Basic TXVmYXNhOkNpcmNsZSBvZiBMaWZl");
        // Digest wins when both are offered, so plain HTTP still signs in.
        let both = authorization(&[basic, RFC7616], &login(), false, &request()).unwrap();
        assert!(both.starts_with("Digest "));
    }

    #[test]
    fn a_responder_counts_nonces_across_answers() {
        let login = login();
        let mut responder = Responder::new(&[RFC7616], &login, false).unwrap();
        let first = responder.answer(&request()).unwrap();
        let second = responder.answer(&request()).unwrap();
        assert!(first.contains("nc=00000001"), "{first}");
        assert!(second.contains("nc=00000002"), "{second}");
    }

    #[test]
    fn a_body_that_cannot_be_signed_is_answered_with_auth_or_not_at_all() {
        let streaming = Signed {
            body: None,
            ..request()
        };
        let login = login();
        let mixed = r#"Digest realm="r", qop="auth,auth-int", nonce="n", algorithm=MD5"#;
        let header = authorization(&[mixed], &login, false, &streaming).unwrap();
        assert!(
            header.contains("qop=auth") && !header.contains("auth-int"),
            "{header}"
        );
        let only = r#"Digest realm="r", qop="auth-int", nonce="n", algorithm=MD5"#;
        let error = authorization(&[only], &login, false, &streaming).unwrap_err();
        assert_eq!(error.kind, ErrorKind::Unsupported);
        // With a body to sign, auth-int is the one used.
        let header = authorization(&[mixed], &login, false, &request()).unwrap();
        assert!(header.contains("auth-int"), "{header}");
    }

    #[test]
    fn unknown_schemes_are_unsupported() {
        let error = authorization(&["Negotiate"], &login(), true, &request()).unwrap_err();
        assert_eq!(error.kind, ErrorKind::Unsupported);
    }
}
