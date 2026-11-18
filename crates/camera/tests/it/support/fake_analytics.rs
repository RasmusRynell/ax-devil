//! The camera's MQTT side for FakeVapix: its MQTT client (`client.cgi`) and its analytics publishers (REST).

use serde_json::{Value, json};

/// What the camera has: the MQTT client's configuration and state, and the analytics publishers.
pub struct Analytics {
    pub config: Value,
    pub active: bool,
    pub publishers: Vec<Value>,
    pub sources: Vec<Value>,
    /// Answer every publisher removal with a failure.
    pub fail_deletes: bool,
    /// Every `configureClient` request's parameters, in order.
    pub configured: Vec<Value>,
}

impl Default for Analytics {
    fn default() -> Self {
        Self {
            config: json!({
                "server": { "host": "old-broker.example", "port": 1883, "protocol": "tcp" },
                "clientId": "old-client",
                "cleanSession": true,
            }),
            sources: vec![
                json!({"key": "com.axis.analytics_scene_description.v0.beta#1"}),
                json!({"key": "com.axis.consolidated_track.v1.beta#1"}),
            ],
            active: false,
            fail_deletes: false,
            configured: Vec::new(),
            publishers: vec![
                json!({ "id": "kept", "data_source_key": "other#1", "mqtt_topic": "other", "qos": 1,
                        "retain": true, "use_topic_prefix": true }),
            ],
        }
    }
}

const CLIENT: &str = "/axis-cgi/mqtt/client.cgi";
const PUBLISHERS: &str = "/config/rest/analytics-mqtt/v1beta/publishers";

impl Analytics {
    /// The JSON answer to a request for the MQTT client or the publishers; `None` for any other path.
    pub fn answer(&mut self, method: &str, path: &str, body: &[u8]) -> Option<String> {
        let body: Value = serde_json::from_slice(body).unwrap_or_default();
        if path == CLIENT {
            return Some(self.client(&body));
        }
        if path == "/config/rest/analytics-mqtt/v1beta/data_sources" {
            return Some(json!({"status": "success", "data": self.sources}).to_string());
        }
        let id = path.strip_prefix(PUBLISHERS)?;
        let success = |data: Value| json!({ "status": "success", "data": data }).to_string();
        Some(match (method, id) {
            ("GET", "") => success(Value::Array(self.publishers.clone())),
            ("POST", "") => {
                self.publishers.push(body["data"].clone());
                success(body["data"].clone())
            }
            ("DELETE", _) if self.fail_deletes => json!({ "status": "error" }).to_string(),
            ("DELETE", id) => {
                let id = id.trim_start_matches('/');
                self.publishers.retain(|publisher| publisher["id"] != id);
                json!({ "status": "success" }).to_string()
            }
            _ => json!({ "status": "error" }).to_string(),
        })
    }

    fn client(&mut self, request: &Value) -> String {
        let data = match request["method"].as_str() {
            Some("getClientStatus") => json!({
                "status": { "state": if self.active { "active" } else { "inactive" } },
                "config": self.config,
            }),
            Some("configureClient") => {
                self.config = request["params"].clone();
                self.configured.push(self.config.clone());
                json!({})
            }
            Some(method) => {
                self.active = method == "activateClient";
                json!({})
            }
            None => return json!({ "error": { "code": 1 } }).to_string(),
        };
        json!({ "apiVersion": "1.0", "data": data }).to_string()
    }
}
