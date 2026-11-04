//! The device API calls: who the camera is, what it offers, a snapshot, and the analytics sources.
//!
//! Endpoints and shapes follow the VAPIX documentation: `basicdeviceinfo.cgi`, `param.cgi`, `jpg/image.cgi` and the
//! `analytics-mqtt` REST API.

use std::fmt;
use std::time::Duration;

use bytes::Bytes;
use reqwest::Method;
use serde_json::{Value, json};

use crate::device::{Call, Device};
use crate::error::{CameraError, ErrorKind};
use crate::target::Head;

/// What an Axis camera says about itself.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Identity {
    /// The product name, such as `Q1656`.
    pub model: String,
    /// The serial number.
    pub serial: String,
    /// The firmware version.
    pub firmware: String,
    /// The product type, such as `Box Camera`.
    pub product_type: String,
    /// The architecture, such as `aarch64`; empty unless the camera gave it, which it does only to [`Device::sign_in`].
    pub architecture: String,
    /// The system on chip; empty unless the camera gave it, like `architecture`.
    pub soc: String,
}

/// A stream size in pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Resolution {
    /// Width in pixels.
    pub width: u32,
    /// Height in pixels.
    pub height: u32,
}

impl fmt::Display for Resolution {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}x{}", self.width, self.height)
    }
}

impl Resolution {
    /// Parses `WIDTHxHEIGHT`; zero sizes and anything else is `None`.
    fn parse(text: &str) -> Option<Self> {
        let (width, height) = text.split_once('x')?;
        let (width, height) = (width.parse().ok()?, height.parse().ok()?);
        (width > 0 && height > 0).then_some(Self { width, height })
    }
}

/// An analytics data source of the camera, the key MQTT publishers are created for.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct SourceKey(pub String);

const BASIC_DEVICE_INFO: &str = "/axis-cgi/basicdeviceinfo.cgi";
const SYSTEM_READY: &str = "/axis-cgi/systemready.cgi";
const PARAM: &str = "/axis-cgi/param.cgi";
const SNAPSHOT: &str = "/axis-cgi/jpg/image.cgi";
/// The most bytes of a snapshot read; a camera's JPEG is a few hundred kilobytes.
const LARGEST_SNAPSHOT: usize = 8 << 20;
/// The analytics-mqtt REST API; the MQTT publisher lease uses the same base.
pub(crate) const ANALYTICS_MQTT: &str = "/config/rest/analytics-mqtt/v1beta";

impl Device {
    /// Asks who the device is without sending any login, so it is safe to call on an unknown address.
    ///
    /// A device that answers otherwise than an Axis camera is [`ErrorKind::NotAxis`]; one that wants a login for this
    /// is [`ErrorKind::LoginRequired`].
    pub async fn identify(&self) -> Result<Identity, CameraError> {
        self.device_info("getAllUnrestrictedProperties", false)
            .await
    }

    /// The same, with the camera's login: a wrong login is [`ErrorKind::LoginRejected`], and no login at all
    /// is [`ErrorKind::LoginRequired`] without a request.
    pub async fn sign_in(&self) -> Result<Identity, CameraError> {
        self.device_info("getAllProperties", true).await
    }

    /// How long the camera has been up, from `systemready.cgi`, which needs no login.
    pub(crate) async fn uptime(&self) -> Result<Duration, CameraError> {
        let request =
            json!({ "apiVersion": "1.0", "method": "systemready", "params": { "timeout": 1 } });
        let body = self
            .call(&Call {
                method: Method::POST,
                path: SYSTEM_READY,
                query: &[],
                json: Some(&request),
                signed: false,
            })
            .await?;
        let reply: Value = serde_json::from_slice(&body).map_err(|_| unreadable_uptime())?;
        parse_uptime(&reply["data"]["uptime"])
    }

    /// The stream sizes `head` offers, from `param.cgi`; empty when the camera lists none.
    pub async fn resolutions(&self, head: Head) -> Result<Vec<Resolution>, CameraError> {
        let query = [("action", "list"), ("group", "Properties.Image")];
        let body = self
            .call(&Call {
                method: Method::GET,
                path: PARAM,
                query: &query,
                json: None,
                signed: true,
            })
            .await?;
        Ok(parse_resolutions(&String::from_utf8_lossy(&body), head))
    }

    /// A JPEG snapshot of `head`, at most 8 MiB: a longer answer is abandoned as it arrives and is
    /// [`ErrorKind::Malformed`].
    pub async fn snapshot(&self, head: Head) -> Result<Bytes, CameraError> {
        let camera = head.number().to_string();
        let query = [("camera", camera.as_str())];
        let body = self
            .call_up_to(
                &Call {
                    method: Method::GET,
                    path: SNAPSHOT,
                    query: &query,
                    json: None,
                    signed: true,
                },
                LARGEST_SNAPSHOT,
            )
            .await?;
        if body.starts_with(&[0xFF, 0xD8, 0xFF]) {
            Ok(body)
        } else {
            Err(CameraError::new(
                ErrorKind::Malformed,
                "the snapshot is not a JPEG",
            ))
        }
    }

    /// The analytics data sources the camera can publish over MQTT.
    pub async fn analytics_sources(&self) -> Result<Vec<SourceKey>, CameraError> {
        let path = format!("{ANALYTICS_MQTT}/data_sources");
        let body = self
            .call(&Call {
                method: Method::GET,
                path: &path,
                query: &[],
                json: None,
                signed: true,
            })
            .await?;
        let reply: Value = serde_json::from_slice(&body)
            .map_err(|_| CameraError::new(ErrorKind::Malformed, "the data sources are not JSON"))?;
        let sources = reply
            .get("data")
            .and_then(Value::as_array)
            .filter(|_| reply["status"] == "success");
        let sources = sources.ok_or_else(|| {
            CameraError::new(ErrorKind::Malformed, "the camera listed no data sources")
        })?;
        Ok(sources
            .iter()
            .filter_map(|source| source["key"].as_str())
            .map(|key| SourceKey(key.to_owned()))
            .collect())
    }

    async fn device_info(&self, method: &str, signed: bool) -> Result<Identity, CameraError> {
        if signed && self.camera.login.is_none() {
            return Err(CameraError::new(ErrorKind::LoginRequired, "no login given"));
        }
        let request = json!({ "apiVersion": "1.3", "method": method });
        let call = Call {
            method: Method::POST,
            path: BASIC_DEVICE_INFO,
            query: &[],
            json: Some(&request),
            signed,
        };
        let body = self.call(&call).await.map_err(|error| match error.kind {
            ErrorKind::NotFound | ErrorKind::Unsupported => {
                CameraError::new(ErrorKind::NotAxis, error.detail())
            }
            _ => error,
        })?;
        parse_identity(&body)
    }
}

/// The uptime a camera reports, as a number or a numeric string; anything a `Duration` cannot hold is unreadable.
fn parse_uptime(uptime: &Value) -> Result<Duration, CameraError> {
    let seconds = uptime
        .as_f64()
        .or_else(|| uptime.as_str().and_then(|text| text.trim().parse().ok()));
    seconds
        .and_then(|seconds| Duration::try_from_secs_f64(seconds).ok())
        .ok_or_else(unreadable_uptime)
}

fn unreadable_uptime() -> CameraError {
    CameraError::new(ErrorKind::Malformed, "the camera gave no uptime")
}

fn not_axis() -> CameraError {
    CameraError::new(
        ErrorKind::NotAxis,
        "the device did not answer as an Axis camera",
    )
}

/// The identity in a `basicdeviceinfo.cgi` answer; anything not from an Axis camera is `NotAxis`.
fn parse_identity(body: &[u8]) -> Result<Identity, CameraError> {
    let reply: Value = serde_json::from_slice(body).map_err(|_| not_axis())?;
    let properties = reply["data"]["propertyList"]
        .as_object()
        .ok_or_else(not_axis)?;
    let text = |name: &str| {
        properties
            .get(name)
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or_default()
    };
    if !text("Brand").eq_ignore_ascii_case("AXIS") {
        return Err(not_axis());
    }
    let model = [text("ProdShortName"), text("ProdFullName"), text("ProdNbr")];
    Ok(Identity {
        model: model
            .into_iter()
            .find(|name| !name.is_empty())
            .unwrap_or_default()
            .to_owned(),
        serial: text("SerialNumber").to_owned(),
        firmware: text("Version").to_owned(),
        product_type: text("ProdType").to_owned(),
        architecture: text("Architecture").to_owned(),
        soc: text("Soc").to_owned(),
    })
}

/// The resolutions `head` offers in a `param.cgi` list of `Properties.Image`.
///
/// The channel's H.264 list wins over its general list, which wins over the camera-wide one. Lines that are not
/// `key=value` (comments, an error body) are ignored, so an answer without values gives none.
fn parse_resolutions(text: &str, head: Head) -> Vec<Resolution> {
    let channel = head.number() - 1;
    let wanted = [
        format!("Properties.Image.I{channel}.H264.Resolution"),
        format!("Properties.Image.I{channel}.Resolution"),
        "Properties.Image.Resolution".to_owned(),
    ];
    let value_of = |name: &str| {
        text.lines()
            .filter(|line| !line.starts_with('#'))
            .filter_map(|line| line.split_once('='))
            .find(|(key, _)| key.strip_prefix("root.").unwrap_or(key) == name)
            .map(|(_, value)| value.trim())
            .filter(|value| !value.is_empty())
    };
    let listed = wanted
        .iter()
        .find_map(|name| value_of(name))
        .unwrap_or_default();
    let mut found: Vec<Resolution> = Vec::new();
    for resolution in listed
        .split(',')
        .filter_map(|token| Resolution::parse(token.trim()))
    {
        if !found.contains(&resolution) {
            found.push(resolution);
        }
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    const PARAMS: &str = "root.Properties.Image.Resolution=1920x1080,1280x720\r\n\
        root.Properties.Image.I0.Resolution=1920x1080,640x360,bogus,0x5\r\n\
        root.Properties.Image.I1.H264.Resolution=800x600,800x600,320x240\r\n\
        # Error: ignored\r\n";

    #[test]
    fn resolutions_follow_the_channel_ladder() {
        let size = |w, h| Resolution {
            width: w,
            height: h,
        };
        // I0 has no H264 list: its general list, with bad tokens dropped.
        assert_eq!(
            parse_resolutions(PARAMS, Head::FIRST),
            [size(1920, 1080), size(640, 360)]
        );
        // I1's H264 list wins, duplicates removed.
        assert_eq!(
            parse_resolutions(PARAMS, Head::new(2).unwrap()),
            [size(800, 600), size(320, 240)]
        );
        // I2 has nothing of its own: the camera-wide list.
        assert_eq!(
            parse_resolutions(PARAMS, Head::new(3).unwrap()),
            [size(1920, 1080), size(1280, 720)]
        );
        assert!(parse_resolutions("# Error: no such group\r\n", Head::FIRST).is_empty());
        assert_eq!(size(640, 360).to_string(), "640x360");
    }

    #[test]
    fn an_uptime_that_is_not_a_duration_is_unreadable_not_a_panic() {
        assert_eq!(
            parse_uptime(&json!("93784")).unwrap(),
            Duration::from_secs(93784)
        );
        for bad in [
            json!(1e300),
            json!("1e300"),
            json!(-1),
            json!("nan"),
            json!(null),
        ] {
            assert_eq!(
                parse_uptime(&bad).unwrap_err().kind,
                ErrorKind::Malformed,
                "{bad}"
            );
        }
    }

    #[test]
    fn identity_needs_an_axis_brand() {
        let axis = br#"{"data":{"propertyList":{"Brand":"AXIS","ProdShortName":"AXIS Q1656","SerialNumber":"B8A44F000001","Version":"11.11.1","ProdType":"Box Camera"}}}"#;
        let identity = parse_identity(axis).unwrap();
        assert_eq!(identity.model, "AXIS Q1656");
        assert_eq!(identity.serial, "B8A44F000001");
        for other in [
            &br#"{"data":{"propertyList":{"Brand":"ACME"}}}"#[..],
            b"<html>",
            b"{}",
        ] {
            assert_eq!(parse_identity(other).unwrap_err().kind, ErrorKind::NotAxis);
        }
    }
}
