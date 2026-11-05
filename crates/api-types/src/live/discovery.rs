//! What the live form asks a camera and the network scan answers: the connection a question is about, the sizes,
//! sources and topics a camera offers, why a question failed, and the cameras the scan found.

use super::{LiveConfirmation, LiveProtocol, LiveReason, WireSecret};
use crate::ErrorWire;
use foundation::ItemId;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// What an Axis camera said about itself without a login.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct NetworkIdentity {
    /// The product name, such as `Q1656`.
    pub model: String,
    /// The serial number.
    pub serial: String,
    /// The firmware version.
    pub firmware: String,
    /// The product type, such as `Box Camera`.
    pub product_type: String,
}

/// Whether the scan signed in to a camera. No password is in any of these.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum NetworkSignIn {
    /// No login was given, or none had a user name.
    NotTried,
    /// A login was accepted.
    SignedIn {
        /// Which of the scan's logins, by position in the `scan.logins` setting.
        login_index: u32,
        /// The architecture, such as `aarch64`; empty if the camera did not say.
        architecture: String,
        /// The system on chip; empty if the camera did not say.
        soc: String,
    },
    /// Every login was turned away.
    Refused,
    /// Signing in did not work.
    Failed,
}

/// An Axis camera the network scan found. Only the owner of a backend with the files grant sees these.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct NetworkRow {
    /// Its IPv4 address.
    pub address: String,
    /// The web protocol that answered.
    pub scheme: LiveProtocol,
    /// The web port that answered.
    pub port: u16,
    /// What it said about itself.
    pub identity: NetworkIdentity,
    /// How long it has been up, in seconds, if it said.
    #[ts(type = "number | null")]
    pub uptime_s: Option<u64>,
    /// The outcome of signing in.
    pub sign_in: NetworkSignIn,
}

/// A stream size a camera offers.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveResolution {
    /// Width in pixels.
    pub width: u32,
    /// Height in pixels.
    pub height: u32,
}

/// The stream sizes one camera head lists of its own.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveHeadResolutions {
    /// The video channel, from 1.
    pub head: u8,
    /// The sizes, in the camera's order; none when the head's list holds no valid size.
    pub resolutions: Vec<LiveResolution>,
}

/// The stream sizes of every head of a camera, the answer of the resolutions route. A head takes its own list when
/// `heads` has one and the camera-wide `common` list otherwise.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveResolutions {
    /// Whether the camera listed any image parameter. An answer without one is worth asking for again.
    pub listed: bool,
    /// The camera-wide sizes, in the camera's order.
    pub common: Vec<LiveResolution>,
    /// The heads with a list of their own.
    pub heads: Vec<LiveHeadResolutions>,
}

/// The analytics data sources a camera publishes, the answer of the sources route.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveSources {
    /// The sources' keys, such as `com.axis.scene.frame.v1#1`.
    pub sources: Vec<String>,
}

/// The camera a question is about. Anything left out takes the configured default, and text may be a `$NAME`
/// reference, which expands under the same rule as the references of a stream.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(default)]
#[ts(optional_fields)]
pub struct LiveConnection {
    /// The camera host or a `$NAME` reference.
    pub host: Option<String>,
    /// The camera user or a reference.
    pub username: Option<String>,
    /// The camera password or a reference.
    pub password: Option<WireSecret>,
    /// The web interface's protocol.
    pub protocol: Option<LiveProtocol>,
    /// The web interface's port, 0 to 65535; 0 or absent is the standard one.
    #[ts(type = "number", optional)]
    pub port: Option<i64>,
    /// The video channel, from 1.
    #[ts(type = "number", optional)]
    pub head: Option<i64>,
    /// A live stream this connection is another of. The backend fills a blank user and password from that stream's
    /// saved login, as it does when the stream is added (see `NewLiveWire::like`), so the question authenticates as
    /// the stream will and no literal password reaches the client.
    #[ts(type = "string", optional)]
    pub like: Option<ItemId>,
}

/// The body of `POST /api/live/resolutions`.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct ResolutionsRequest {
    /// The camera and head to ask.
    #[serde(default)]
    pub connection: LiveConnection,
}

/// What kind of overlay the sources are for.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum SourcesOverlay {
    /// The analytics data sources the camera can publish over MQTT.
    Mqtt,
    /// The topics of the camera's DataHub.
    DataHub,
}

/// The body of `POST /api/live/sources`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct SourcesRequest {
    /// The camera to ask.
    #[serde(default)]
    pub connection: LiveConnection,
    /// Which list to return.
    pub overlay: SourcesOverlay,
}

/// The DataHub topics a camera offers.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveTopics {
    /// The topics' names.
    pub topics: Vec<String>,
}

/// The answer of `POST /api/live/sources`: the list the request's overlay asked for.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "overlay", rename_all = "lowercase")]
pub enum SourcesAnswer {
    /// The MQTT analytics data sources.
    Mqtt(LiveSources),
    /// The DataHub topics.
    DataHub(LiveTopics),
}

/// Why a question to a camera got no answer: the body of a failed `POST /api/live/resolutions` or
/// `POST /api/live/sources`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "problem")]
pub enum LiveProblem {
    /// The connection cannot be used as entered (status 422); `error.location` names the field.
    Invalid {
        /// What to fix.
        error: ErrorWire,
    },
    /// A reference would send a login to a host the user has not allowed (status 409): confirm it, then ask again.
    NeedsConfirmation {
        /// What to ask the user to confirm.
        confirmation: LiveConfirmation,
    },
    /// The camera did not answer, or answered no (status 502).
    Camera {
        /// Why, as a code the client words.
        reason: LiveReason,
    },
}

/// The body of `POST /api/network/scan`.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(default)]
#[ts(optional_fields)]
pub struct ScanRequestWire {
    /// The addresses and ranges to scan, such as `192.168.1.0/24`. Blank is the local network; absent is the
    /// `scan.range` setting.
    pub range: Option<String>,
}

/// The cameras the scan has found so far; one line of the `GET /api/network` stream.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct NetworkSnapshot {
    /// Counts the scans started; rows of an older scan are never shown with a newer number.
    #[ts(type = "number")]
    pub scan: u64,
    /// Whether the scan is still running.
    pub scanning: bool,
    /// The cameras, by address.
    pub rows: Vec<NetworkRow>,
}
