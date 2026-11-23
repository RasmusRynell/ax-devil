//! The camera's side of MQTT analytics: its MQTT client (`client.cgi`) and its analytics publishers (REST).

use reqwest::Method;
use serde_json::{Value, json};

use crate::device::{Call, Device};
use crate::error::{CameraError, ErrorKind};
use crate::vapix::{ANALYTICS_MQTT, SourceKey};

const CLIENT: &str = "/axis-cgi/mqtt/client.cgi";

/// An analytics publisher configured on the camera: what it publishes, and where.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Publisher {
    /// The camera's identifier for it.
    pub id: String,
    /// The analytics data source it publishes.
    pub source: SourceKey,
    /// The MQTT topic it publishes on.
    pub topic: String,
    /// The MQTT quality of service.
    pub qos: u64,
    /// Whether messages are retained by the broker.
    pub retain: bool,
    /// Whether the camera prefixes the topic with its own.
    pub use_topic_prefix: bool,
}

impl Publisher {
    fn parse(value: &Value) -> Option<Self> {
        Some(Self {
            id: value["id"].as_str()?.to_owned(),
            source: SourceKey(value["data_source_key"].as_str()?.to_owned()),
            topic: value["mqtt_topic"].as_str()?.to_owned(),
            qos: value["qos"].as_u64().unwrap_or(0),
            retain: value["retain"].as_bool().unwrap_or(false),
            use_topic_prefix: value["use_topic_prefix"].as_bool().unwrap_or(false),
        })
    }
}

fn malformed(detail: &str) -> CameraError {
    CameraError::new(ErrorKind::Malformed, detail)
}

impl Device {
    /// The analytics publishers configured on the camera.
    pub async fn analytics_publishers(&self) -> Result<Vec<Publisher>, CameraError> {
        let data = self
            .analytics_rest(Method::GET, "/publishers", None)
            .await?;
        let list = data
            .as_array()
            .ok_or_else(|| malformed("the publishers are not a list"))?;
        Ok(list.iter().filter_map(Publisher::parse).collect())
    }

    /// Adds `publisher` (qos and retain as given) to the camera.
    pub(crate) async fn create_publisher(&self, publisher: &Publisher) -> Result<(), CameraError> {
        let body = json!({ "data": {
            "id": publisher.id,
            "data_source_key": publisher.source.0,
            "mqtt_topic": publisher.topic,
            "qos": publisher.qos,
            "retain": publisher.retain,
            "use_topic_prefix": publisher.use_topic_prefix,
        }});
        self.analytics_rest(Method::POST, "/publishers", Some(&body))
            .await
            .map(drop)
    }

    /// Removes the publisher `id` from the camera. Ids are made by this crate and need no escaping.
    pub(crate) async fn remove_publisher(&self, id: &str) -> Result<(), CameraError> {
        self.analytics_rest(Method::DELETE, &format!("/publishers/{id}"), None)
            .await
            .map(drop)
    }

    /// The camera's MQTT client: its `status` (state) and `config`, as `getClientStatus` answers.
    pub(crate) async fn mqtt_client_state(&self) -> Result<Value, CameraError> {
        let state = self.mqtt_client("getClientStatus", None).await?;
        if state.is_object() {
            Ok(state)
        } else {
            Err(malformed("the camera gave no MQTT client state"))
        }
    }

    /// Sets the camera's MQTT client configuration (a `config` as `getClientStatus` gives it, or a new one).
    pub(crate) async fn mqtt_client_configure(&self, config: &Value) -> Result<(), CameraError> {
        self.mqtt_client("configureClient", Some(config))
            .await
            .map(drop)
    }

    /// Starts or stops the camera's MQTT client.
    pub(crate) async fn mqtt_client_activate(&self, active: bool) -> Result<(), CameraError> {
        let method = if active {
            "activateClient"
        } else {
            "deactivateClient"
        };
        self.mqtt_client(method, None).await.map(drop)
    }

    /// One `client.cgi` call; the answer's `data`. An `error` in an answer with a good status is a fault.
    async fn mqtt_client(
        &self,
        method: &str,
        params: Option<&Value>,
    ) -> Result<Value, CameraError> {
        let mut request = json!({ "apiVersion": "1.0", "method": method });
        if let Some(params) = params {
            request["params"] = params.clone();
        }
        let body = self
            .call(&Call {
                method: Method::POST,
                path: CLIENT,
                query: &[],
                json: Some(&request),
                signed: true,
            })
            .await?;
        let reply: Value = serde_json::from_slice(&body)
            .map_err(|_| malformed("the MQTT client answer is not JSON"))?;
        if reply.get("error").is_some() {
            let code = reply["error"]["code"].as_i64().unwrap_or_default();
            return Err(CameraError::new(
                ErrorKind::DeviceFault,
                format!("the MQTT client call {method} failed with code {code}"),
            ));
        }
        Ok(reply["data"].clone())
    }

    /// One analytics-mqtt REST call; the answer's `data` (null when there is none).
    async fn analytics_rest(
        &self,
        method: Method,
        path: &str,
        json: Option<&Value>,
    ) -> Result<Value, CameraError> {
        let path = format!("{ANALYTICS_MQTT}{path}");
        let body = self
            .call(&Call {
                method,
                path: &path,
                query: &[],
                json,
                signed: true,
            })
            .await?;
        let reply: Value =
            serde_json::from_slice(&body).map_err(|_| malformed("the answer is not JSON"))?;
        if reply["status"] == "success" {
            Ok(reply["data"].clone())
        } else {
            Err(CameraError::new(
                ErrorKind::DeviceFault,
                "the camera did not report success",
            ))
        }
    }
}
