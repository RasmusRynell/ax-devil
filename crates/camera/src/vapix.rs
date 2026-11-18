//! The device API calls: who the camera is, what it offers, a snapshot, and the analytics sources.
//!
//! Endpoints and shapes follow the VAPIX documentation: `basicdeviceinfo.cgi`, `param.cgi`, `jpg/image.cgi` and the
//! `analytics-mqtt` REST API.

use std::collections::BTreeMap;
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
    /// Parses `WIDTHxHEIGHT`, two positive whole numbers without a sign or leading zero; anything else is `None`.
    fn parse(text: &str) -> Option<Self> {
        let (width, height) = text.split_once('x')?;
        let number = |digits: &str| {
            let plain =
                !digits.starts_with('0') && digits.bytes().all(|byte| byte.is_ascii_digit());
            plain.then(|| digits.parse().ok()).flatten()
        };
        Some(Self {
            width: number(width)?,
            height: number(height)?,
        })
    }
}

/// The stream sizes a camera lists under `Properties.Image`, for every head at once.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ImageSizes {
    /// Whether the answer held any parameter; an error or an empty body held none and is worth asking again.
    pub listed: bool,
    /// The camera-wide sizes, which a head without a list of its own offers.
    pub common: Vec<Resolution>,
    /// The heads with a list of their own, in head order; a list can come to no valid size.
    pub heads: Vec<(Head, Vec<Resolution>)>,
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

    /// The stream sizes of every head, from one `param.cgi` list of `Properties.Image`.
    pub async fn image_sizes(&self) -> Result<ImageSizes, CameraError> {
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
        Ok(parse_image_sizes(&String::from_utf8_lossy(&body)))
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
            .filter(|key| !key.is_empty())
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
            // With a login, `Unsupported` may be the login's scheme that cannot be answered, not the device.
            ErrorKind::NotFound => CameraError::new(ErrorKind::NotAxis, error.detail()),
            ErrorKind::Unsupported if !signed => {
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

/// The sizes in a `param.cgi` list of `Properties.Image`.
///
/// A head's H.264 list wins over its general list, which wins over the camera-wide one; the first list that is not
/// blank is the one, even when none of its sizes is valid. Lines that are not `key=value` (comments, an error body)
/// are ignored, so an answer without values lists nothing.
fn parse_image_sizes(text: &str) -> ImageSizes {
    let values: BTreeMap<&str, &str> = text
        .lines()
        .filter(|line| !line.starts_with('#'))
        .filter_map(|line| line.split_once('='))
        .map(|(key, value)| (key.strip_prefix("root.").unwrap_or(key), value))
        .collect();
    let first_list = |names: &[String]| {
        names
            .iter()
            .find_map(|name| values.get(name.as_str()).filter(|value| !value.is_empty()))
            .map(|list| sizes_in(list))
    };
    ImageSizes {
        listed: !values.is_empty(),
        common: first_list(&["Properties.Image.Resolution".to_owned()]).unwrap_or_default(),
        heads: (1..=Head::MAX)
            .filter_map(Head::new)
            .filter_map(|head| {
                let channel = head.number() - 1;
                let sizes = first_list(&[
                    format!("Properties.Image.I{channel}.H264.Resolution"),
                    format!("Properties.Image.I{channel}.Resolution"),
                ])?;
                Some((head, sizes))
            })
            .collect(),
    }
}

/// The valid sizes of a comma-separated list, in order and without repeats.
fn sizes_in(list: &str) -> Vec<Resolution> {
    let mut found: Vec<Resolution> = Vec::new();
    for resolution in list
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
        root.Properties.Image.I299.Resolution=320x180\r\n\
        # Error: ignored\r\n";

    #[test]
    fn every_head_gets_its_sizes_from_one_list() {
        let size = |w, h| Resolution {
            width: w,
            height: h,
        };
        let sizes = parse_image_sizes(PARAMS);
        assert!(sizes.listed);
        assert_eq!(sizes.common, [size(1920, 1080), size(1280, 720)]);
        // I0 has no H264 list: its general list, with bad tokens dropped. I1's H264 list wins, duplicates removed.
        // The third head has none of its own and so takes the camera-wide list.
        assert_eq!(
            sizes.heads,
            [
                (Head::FIRST, vec![size(1920, 1080), size(640, 360)]),
                (Head::new(2).unwrap(), vec![size(800, 600), size(320, 240)]),
                (Head::new(300).unwrap(), vec![size(320, 180)]),
            ]
        );
        assert_eq!(size(640, 360).to_string(), "640x360");
    }

    #[test]
    fn a_list_of_no_valid_size_is_the_heads_own_and_a_list_of_no_value_is_none() {
        let sizes = parse_image_sizes(
            "root.Properties.Image.Resolution=640x360\r\nroot.Properties.Image.I0.Resolution=bogus,+5x3,05x3\r\n",
        );
        assert_eq!(sizes.heads, [(Head::FIRST, vec![])]);
        let unrelated = parse_image_sizes("root.Properties.Image.Format=jpeg\r\n");
        assert!(unrelated.listed && unrelated.common.is_empty() && unrelated.heads.is_empty());
        assert!(!parse_image_sizes("# Error: no such group\r\n").listed);
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
