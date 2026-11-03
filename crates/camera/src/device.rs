//! The camera's web interface over HTTP(S): one client, Digest sign-in on demand, errors typed from statuses.

use bytes::Bytes;
use reqwest::header::{AUTHORIZATION, CONTENT_TYPE, HeaderValue, WWW_AUTHENTICATE};
use reqwest::redirect::Policy;
use reqwest::{Client, Method, StatusCode, Url};

use crate::auth::{Signed, authorization};
use crate::error::{CameraError, ErrorKind};
use crate::target::{Camera, Scheme};
use crate::tls;

/// A connection to a camera's web interface (the device API).
///
/// A login is sent only in answer to the camera's 401 challenge, never in a URL. Redirects are not followed: an
/// answer from elsewhere is [`ErrorKind::InvalidTarget`].
#[derive(Debug)]
pub struct Device {
    client: Client,
    pub(crate) camera: Camera,
    digest_only: bool,
}

/// One request to the device API.
pub(crate) struct Call<'a> {
    pub(crate) method: Method,
    pub(crate) path: &'a str,
    pub(crate) query: &'a [(&'a str, &'a str)],
    pub(crate) json: Option<&'a serde_json::Value>,
    /// Whether to answer a 401 with the camera's login; otherwise a 401 is [`ErrorKind::LoginRequired`].
    pub(crate) signed: bool,
}

impl Device {
    /// A client for `camera`; no request is made.
    pub fn new(camera: &Camera) -> Result<Self, CameraError> {
        let client = Client::builder()
            .tls_backend_preconfigured(tls::client_config()?)
            .redirect(Policy::none())
            .no_proxy()
            .timeout(camera.timeout)
            .connect_timeout(camera.timeout)
            .build()
            .map_err(|error| transport(&error))?;
        Ok(Self {
            client,
            camera: camera.clone(),
            digest_only: false,
        })
    }

    /// The same device, answering a challenge with Digest only: Basic is refused even over HTTPS, so a wrong login is
    /// refused at once instead of being retried with Basic.
    #[must_use]
    pub fn digest_only(mut self) -> Self {
        self.digest_only = true;
        self
    }

    /// Sends `call` and returns the body of a 2xx answer.
    pub(crate) async fn call(&self, call: &Call<'_>) -> Result<Bytes, CameraError> {
        self.call_up_to(call, usize::MAX).await
    }

    /// Like [`call`](Self::call), but stops reading the body once it exceeds `limit` bytes, so a camera cannot make
    /// this process hold more; that is [`ErrorKind::Malformed`].
    pub(crate) async fn call_up_to(
        &self,
        call: &Call<'_>,
        limit: usize,
    ) -> Result<Bytes, CameraError> {
        let url = self.url(call)?;
        let body = call.json.map(|json| Bytes::from(json.to_string()));
        let mut response = self.send(call, &url, body.as_ref(), None).await?;
        let mut login_sent = false;
        if response.status() == StatusCode::UNAUTHORIZED {
            let login = self.camera.login.as_ref().filter(|_| call.signed);
            let Some(login) = login else {
                return Err(CameraError::new(ErrorKind::LoginRequired, "HTTP 401"));
            };
            let challenges: Vec<&str> = response
                .headers()
                .get_all(WWW_AUTHENTICATE)
                .iter()
                .filter_map(|value| value.to_str().ok())
                .collect();
            let uri = match url.query() {
                Some(query) => format!("{}?{query}", url.path()),
                None => url.path().to_owned(),
            };
            let signed = Signed {
                method: call.method.as_str(),
                uri: &uri,
                body: body.as_deref().unwrap_or_default(),
            };
            let mut header = HeaderValue::from_str(&authorization(
                &challenges,
                login,
                !self.digest_only && self.camera.web.scheme == Scheme::Https,
                &signed,
            )?)
            .map_err(|_| {
                CameraError::new(
                    ErrorKind::Malformed,
                    "the authentication challenge cannot be answered",
                )
            })?;
            header.set_sensitive(true);
            response = self.send(call, &url, body.as_ref(), Some(header)).await?;
            login_sent = true;
        }
        let status = response.status();
        if status.is_success() {
            return read_up_to(response, limit).await;
        }
        let kind = if status == StatusCode::UNAUTHORIZED && login_sent {
            ErrorKind::LoginRejected
        } else {
            ErrorKind::of_status(status.as_u16())
        };
        Err(CameraError::new(kind, format!("HTTP {}", status.as_u16())))
    }

    fn url(&self, call: &Call<'_>) -> Result<Url, CameraError> {
        let web = self.camera.web;
        let base = format!(
            "{}://{}:{}{}",
            web.scheme.as_str(),
            self.camera.host,
            web.port,
            call.path
        );
        let mut url = Url::parse(&base).map_err(|_| {
            CameraError::new(
                ErrorKind::InvalidTarget,
                "the camera address is not a valid URL",
            )
        })?;
        if !call.query.is_empty() {
            url.query_pairs_mut().extend_pairs(call.query);
        }
        Ok(url)
    }

    async fn send(
        &self,
        call: &Call<'_>,
        url: &Url,
        body: Option<&Bytes>,
        authorization: Option<HeaderValue>,
    ) -> Result<reqwest::Response, CameraError> {
        let mut request = self.client.request(call.method.clone(), url.clone());
        if let Some(body) = body {
            request = request
                .header(CONTENT_TYPE, "application/json")
                .body(body.clone());
        }
        if let Some(authorization) = authorization {
            request = request.header(AUTHORIZATION, authorization);
        }
        request.send().await.map_err(|error| transport(&error))
    }
}

/// The body of `response`, read chunk by chunk and abandoned as soon as it is longer than `limit`.
async fn read_up_to(mut response: reqwest::Response, limit: usize) -> Result<Bytes, CameraError> {
    let too_long = || {
        CameraError::new(
            ErrorKind::Malformed,
            format!("the answer is longer than {limit} bytes"),
        )
    };
    if response
        .content_length()
        .is_some_and(|length| length > limit as u64)
    {
        return Err(too_long());
    }
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|error| transport(&error))? {
        if chunk.len() > limit - body.len() {
            return Err(too_long());
        }
        body.extend_from_slice(&chunk);
    }
    Ok(body.into())
}

/// The kind of a transport failure, from what the client reports (never from its text), with its causes as detail.
///
/// The URL is dropped from the detail; it holds no credentials but the detail is shown to people.
fn transport(error: &reqwest::Error) -> CameraError {
    let kind = if error.is_timeout() {
        ErrorKind::TimedOut
    } else if error.is_connect() {
        ErrorKind::Unreachable
    } else if error.is_builder() {
        ErrorKind::InvalidTarget
    } else {
        ErrorKind::Lost
    };
    let causes: Vec<String> =
        std::iter::successors(std::error::Error::source(error), |cause| cause.source())
            .map(ToString::to_string)
            .collect();
    let detail = causes.join(": ");
    CameraError::new(kind, detail)
}
