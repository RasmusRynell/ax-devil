//! The DataHub JSON-RPC messages: what is sent, and what an incoming text frame can be.

use serde_json::{Value, json};

use crate::error::{CameraError, ErrorKind};

pub(super) const SAMPLE_METHOD: &str = "data-hub_v1:topicSample";
pub(super) const SUBSCRIBE: &str = "data-hub_v1:subscribe";
pub(super) const LIST_TOPICS: &str = "data-hub_v1:listTopics";

/// A failure of the JSON-RPC exchange. The camera answered, but not in a way another try would change, so it is not
/// retryable.
pub(super) fn protocol(detail: &str) -> CameraError {
    CameraError::new(ErrorKind::Unsupported, detail)
}

/// The subscription to one topic on one channel, from now on.
pub(super) fn subscribe_params(topic: &str, channel: u32) -> Value {
    json!({
        "filters": [{ "topics": [topic], "instanceFilter": [{ "channel_id": channel }] }],
        "startFrom": { "position": "new" },
    })
}

/// A JSON-RPC request as text.
pub(super) fn request(id: &str, method: &str, params: &Value) -> String {
    json!({ "jsonrpc": "2.0", "id": id, "method": method, "params": params }).to_string()
}

/// One topic sample: its topic and its `data`, once the envelope has been checked.
#[derive(Debug, PartialEq)]
pub(super) struct Sample {
    pub(super) topic: String,
    pub(super) data: Value,
}

/// What an incoming JSON message is.
#[derive(Debug, PartialEq)]
pub(super) enum Incoming {
    Sample(Sample),
    /// An answer to a request (it has no `method`).
    Response(Value),
    /// A notification this crate does not use.
    Ignored,
}

/// Classifies one text frame; anything that is not a JSON object, or a sample with a damaged envelope, is a protocol
/// failure.
pub(super) fn parse(text: &str) -> Result<Incoming, CameraError> {
    let message: Value =
        serde_json::from_str(text).map_err(|_| protocol("the camera sent invalid JSON"))?;
    if !message.is_object() {
        return Err(protocol(
            "the camera sent a JSON message that is not an object",
        ));
    }
    // A camera's answer echoes the request's `method`, so an answer is told by its `result` or `error`.
    if message.get("result").is_some() || message.get("error").is_some() {
        return Ok(Incoming::Response(message));
    }
    match message.get("method") {
        None => Ok(Incoming::Response(message)),
        Some(method) if method == SAMPLE_METHOD => sample(&message["params"]).map(Incoming::Sample),
        Some(_) => Ok(Incoming::Ignored),
    }
}

/// The envelope of a sample: a topic, an integer channel, an object `data`, a timestamp and a historical flag.
fn sample(params: &Value) -> Result<Sample, CameraError> {
    let topic = params["topic"].as_str().filter(|topic| !topic.is_empty());
    let channel =
        params["instance"]["channel_id"].is_i64() || params["instance"]["channel_id"].is_u64();
    let stamped = params["timestamp"]
        .as_str()
        .is_some_and(|stamp| !stamp.is_empty());
    match (
        topic,
        channel,
        params["data"].is_object(),
        stamped,
        params["isHistorical"].is_boolean(),
    ) {
        (Some(topic), true, true, true, true) => Ok(Sample {
            topic: topic.to_owned(),
            data: params["data"].clone(),
        }),
        _ => Err(protocol("a DataHub sample has a damaged envelope")),
    }
}

/// The `result` of a response to the request `id`; an error, or a missing result, is a protocol failure.
pub(super) fn result(response: &Value, id: &str, method: &str) -> Result<Value, CameraError> {
    if response["id"] != id {
        return Err(protocol("a DataHub response does not match the request"));
    }
    if let Some(error) = response.get("error") {
        let code = error["code"].as_i64().unwrap_or_default();
        return Err(protocol(&format!(
            "DataHub {method} failed with error code {code}"
        )));
    }
    response
        .get("result")
        .cloned()
        .ok_or_else(|| protocol(&format!("the DataHub response to {method} has no result")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_text(extra: Value) -> String {
        let mut params = json!({
            "topic": "t", "instance": { "channel_id": 1 }, "data": { "a": 1 },
            "timestamp": "2026-01-01T00:00:00Z", "isHistorical": false,
        });
        if let (Some(params), Some(extra)) = (params.as_object_mut(), extra.as_object()) {
            params.extend(extra.clone());
        }
        json!({ "method": SAMPLE_METHOD, "params": params }).to_string()
    }

    #[test]
    fn the_subscription_names_one_topic_and_channel_from_now() {
        assert_eq!(
            subscribe_params("chosen", 7).to_string(),
            r#"{"filters":[{"instanceFilter":[{"channel_id":7}],"topics":["chosen"]}],"startFrom":{"position":"new"}}"#
        );
    }

    #[test]
    fn a_damaged_envelope_is_a_protocol_failure_and_other_notifications_are_ignored() {
        assert!(matches!(
            parse(&sample_text(json!({}))),
            Ok(Incoming::Sample(_))
        ));
        for damage in [
            json!({ "topic": "" }),
            json!({ "instance": { "channel_id": "1" } }),
            json!({ "data": [1] }),
            json!({ "timestamp": "" }),
            json!({ "isHistorical": "no" }),
        ] {
            let error = parse(&sample_text(damage)).unwrap_err();
            assert!(!error.kind.retryable(), "{error}");
        }
        for text in ["nope", "[1]"] {
            assert_eq!(parse(text).unwrap_err().kind, ErrorKind::Unsupported);
        }
        assert_eq!(parse(r#"{"method":"other"}"#), Ok(Incoming::Ignored));
        assert!(matches!(
            parse(r#"{"id":"1","result":{}}"#),
            Ok(Incoming::Response(_))
        ));
        // The real camera echoes the method in its answers.
        assert!(matches!(
            parse(r#"{"id":"1","method":"data-hub_v1:listTopics","result":{}}"#),
            Ok(Incoming::Response(_))
        ));
    }
}
