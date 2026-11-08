//! What an RTSP failure says: a closed vocabulary of fixed phrases.
//!
//! Retina's error text quotes what it was given and what the camera answered (the request URL with its query, a
//! header value, a response body), and a `CameraError` detail is shown to people and written to logs. So no word of
//! retina's text is ever copied: [`Cause::of`] reads it only to choose one of the variants below, and [`Cause::words`]
//! returns constants and numbers. A leak is impossible by construction, whatever the camera echoes back.

use crate::error::ErrorKind;

/// Where in an RTSP connection a step failed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Step {
    /// The DESCRIBE that opens the session; a failure without a status is a connection that was never made.
    Describe,
    /// Setting up one stream.
    Setup,
    /// Starting playback.
    Play,
    /// Reading the playing stream.
    Stream,
}

impl Step {
    /// The kind of a failure that has no response status: what the step failing at this point means.
    pub(super) const fn otherwise(self) -> ErrorKind {
        match self {
            Self::Describe => ErrorKind::Unreachable,
            Self::Setup | Self::Play | Self::Stream => ErrorKind::Lost,
        }
    }

    const fn at(self) -> &'static str {
        match self {
            Self::Describe => "while describing the stream",
            Self::Setup => "while setting up the stream",
            Self::Play => "while starting the stream",
            Self::Stream => "while playing the stream",
        }
    }
}

/// Why a step failed, as far as it is worth telling.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Cause {
    /// A response with this status.
    Status(u16),
    /// The answer is not valid RTSP.
    Framing,
    /// The answer to DESCRIBE has a content type that is not a stream description.
    ContentType,
    /// The stream description or a header of the answer cannot be understood.
    Description,
    /// No connection could be made.
    Connect,
    /// The connection broke while reading or writing.
    Io,
    /// Retina gave up waiting.
    Timeout,
    /// The camera uses something retina does not support.
    Unsupported,
    /// Anything else.
    Other,
}

impl Cause {
    /// The variant for retina's `status` and message `text`. The text only selects; none of it is kept.
    pub(super) fn of(status: Option<u16>, text: &str) -> Self {
        let first = text.lines().next().unwrap_or_default();
        if first.contains("content type") {
            Self::ContentType
        } else if let Some(status) = status {
            Self::Status(status)
        } else if first.starts_with("RTSP framing error") {
            Self::Framing
        } else if first.starts_with("Unable to connect") {
            Self::Connect
        } else if first.starts_with("Error reading from") || first.starts_with("Error writing to") {
            Self::Io
        } else if first.starts_with("Timeout") {
            Self::Timeout
        } else if first.starts_with("Unsupported") {
            Self::Unsupported
        } else if first.contains("SDP") || first.contains("Unparseable") || first.contains("header")
        {
            Self::Description
        } else {
            Self::Other
        }
    }

    /// Fixed words for the cause at `step`; only a status code is a number of the camera's own.
    pub(super) fn words(self, step: Step) -> String {
        let at = step.at();
        match self {
            Self::Status(status) => format!("the camera answered {status} {at}"),
            Self::Framing => format!("the camera's answer is not valid RTSP {at}"),
            Self::ContentType => format!("the camera's answer has an unexpected content type {at}"),
            Self::Description => format!("the camera's answer cannot be understood {at}"),
            Self::Connect => format!("no connection could be made {at}"),
            Self::Io => format!("the connection broke {at}"),
            Self::Timeout => format!("the camera did not answer in time {at}"),
            Self::Unsupported => format!("the camera uses something that is not supported {at}"),
            Self::Other => format!("the stream failed {at}"),
        }
    }
}

/// The words for a retina failure at `step`.
pub(super) fn words(step: Step, error: &retina::Error) -> String {
    Cause::of(error.status_code(), &error.to_string()).words(step)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Whatever retina quotes, the words hold none of it: the URL with its query, a path parameter or a userinfo, a
    /// header value echoed back, a body.
    #[test]
    fn the_words_of_a_failure_are_constants_whatever_retina_quotes() {
        let hostile = [
            "200 response to DESCRIBE CSeq=2: DESCRIBE response at rtsp://root:URLPASS@127.0.0.1:554/s?token=QUERYSECRET has unexpected content type /s;session=PATHSECRET\\n\\nconn: x",
            "RTSP framing error: bad line SECRET: nope",
            "Unable to connect to RTSP server: rtsp://u:URLPASS@h/s?token=SECRET",
            "Error reading from RTSP peer: SECRET\\n\\nconn: SECRET",
            "Unsupported: codec SECRET",
            "Failed precondition: SECRET",
            "Invalid argument: SECRET header SDP",
            "401 response to DESCRIBE CSeq=1: SECRET",
        ];
        for text in hostile {
            for status in [None, Some(401), Some(200)] {
                for step in [Step::Describe, Step::Setup, Step::Play, Step::Stream] {
                    let words = Cause::of(status, text).words(step);
                    for secret in [
                        "SECRET",
                        "URLPASS",
                        "token",
                        "session",
                        "127.0.0.1",
                        "rtsp://",
                    ] {
                        assert!(!words.contains(secret), "{secret} in {words} from {text}");
                    }
                }
            }
        }
    }

    #[test]
    fn a_failure_still_says_what_kind_it_was_and_where() {
        let said = |status, text, step| Cause::of(status, text).words(step);
        assert_eq!(
            said(
                Some(200),
                "200 response to DESCRIBE CSeq=2: DESCRIBE response at x has unexpected content type y",
                Step::Describe
            ),
            "the camera's answer has an unexpected content type while describing the stream"
        );
        assert_eq!(
            said(
                Some(404),
                "404 response to DESCRIBE CSeq=1: x",
                Step::Describe
            ),
            "the camera answered 404 while describing the stream"
        );
        assert_eq!(
            said(None, "Error reading from RTSP peer: x", Step::Stream),
            "the connection broke while playing the stream"
        );
        assert_eq!(Step::Describe.otherwise(), ErrorKind::Unreachable);
        assert_eq!(Step::Play.otherwise(), ErrorKind::Lost);
    }
}
