//! Wire types of live camera streams: what an item resolves to, what the user asks to add, the playback socket's live
//! messages, and the answers of the live routes.
//!
//! Nothing here carries the camera's host, a login or any text from the device or the connection: a reason is a
//! [`LiveReason`] code, which the client words, and the details stay in the backend's log. A live feed is a playback
//! socket opened with a ticket like any other stream; it differs from a recording in four ways:
//!
//! - Its frames are VideoFrame envelopes (kind 1) whose `timestamp_us` is the capture time, followed by one FrameMap
//!   run of one cell for the lane; there is no Timeline, no frame count and no seeking.
//! - It opens with `LiveOpened` instead of `Opened`, and a holder with `Own` authority may send `Live` actions; one
//!   that follows (`Follow` authority) only watches.
//! - `LiveStatus` reports each feed; `LiveEvents` and `LiveGap` carry the Events list, which keeps recording while the
//!   lane is hidden.
//! - Nothing starts until a viewer is open: opening the item's viewer is Start and closing it is Stop.

use crate::{
    catalogs::CatalogInfoWire,
    playback::{LaneDefaultsWire, LaneInfo},
};
use foundation::ItemId;
use scene::{Event, OperationKind};
use serde::{Deserialize, Serialize};
use std::fmt;
use ts_rs::TS;
use workspace::{LiveOverlay, LiveRecipe, Protocol, Secret, intake};

mod discovery;
mod glance;
mod kept;
pub use discovery::*;
pub use glance::*;
pub use kept::*;

/// The protocol of a camera's web interface.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum LiveProtocol {
    /// Plain HTTP.
    Http,
    /// HTTP over TLS.
    Https,
}

impl From<LiveProtocol> for Protocol {
    fn from(protocol: LiveProtocol) -> Self {
        match protocol {
            LiveProtocol::Http => Self::Http,
            LiveProtocol::Https => Self::Https,
        }
    }
}

impl From<Protocol> for LiveProtocol {
    fn from(protocol: Protocol) -> Self {
        match protocol {
            Protocol::Http => Self::Http,
            Protocol::Https => Self::Https,
        }
    }
}

/// Where a live video's overlay comes from. At most one per video.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum LiveOverlayKind {
    /// Video only.
    None,
    /// Analytics metadata inside the RTSP stream.
    Rtsp,
    /// Analytics the camera publishes to an MQTT broker.
    Mqtt,
    /// A DataHub topic of the camera.
    DataHub,
}

impl From<&LiveOverlay> for LiveOverlayKind {
    fn from(overlay: &LiveOverlay) -> Self {
        match overlay {
            LiveOverlay::None => Self::None,
            LiveOverlay::Rtsp { .. } => Self::Rtsp,
            LiveOverlay::Mqtt { .. } => Self::Mqtt,
            LiveOverlay::DataHub { .. } => Self::DataHub,
        }
    }
}

/// Why a feed is not live: the kind of the camera error, as a stable code the client words. Never the device's own
/// text, which can name the host.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub enum LiveReason {
    /// The camera wants a login and none was given.
    LoginRequired,
    /// The camera turned the login away.
    LoginRejected,
    /// The login is accepted but not allowed to do this.
    Forbidden,
    /// The device answered, but not as an Axis camera.
    NotAxis,
    /// The device has no such resource.
    NotFound,
    /// The device cannot do this.
    Unsupported,
    /// The MQTT broker does not speak the protocol version offered.
    BrokerUnsupportedProtocol,
    /// The MQTT broker rejected the client identifier.
    BrokerClientIdRejected,
    /// The MQTT broker rejected the user name or password.
    BrokerBadCredentials,
    /// The MQTT broker does not authorise this client.
    BrokerNotAuthorized,
    /// The camera's MQTT client is in use by something else.
    Busy,
    /// The camera's MQTT client settings could not be put back afterwards, so they were not touched.
    Unrestorable,
    /// The camera address is not usable as given.
    InvalidTarget,
    /// No connection could be made.
    Unreachable,
    /// The device did not answer in time.
    TimedOut,
    /// An established connection ended.
    Lost,
    /// The stream stopped delivering.
    Silent,
    /// The device sent something that does not parse.
    Malformed,
    /// The device is turning requests away for now.
    Throttled,
    /// The device failed.
    DeviceFault,
}

/// Where one feed (the video, or the overlay) stands.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "status")]
pub enum LiveFeedStatus {
    /// The first connection is being made.
    Connecting,
    /// Data flows.
    Live,
    /// The connection is up and nothing arrives. Not an error: a DataHub topic may be silent.
    Stalled {
        /// How many seconds of silence make a feed stalled.
        seconds: u32,
    },
    /// The connection failed and is retried by itself.
    Reconnecting {
        /// Which retry this is, from 1; reset when data flows again.
        attempt: u32,
        /// Why the last attempt failed.
        reason: LiveReason,
    },
    /// The connection failed and retrying cannot fix it; only a `Retry` action does.
    Failed {
        /// Why.
        reason: LiveReason,
    },
}

/// What a holder of a live stream asks of its feed.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum LiveAction {
    /// Stop delivering to this viewer, keeping the connection.
    Pause,
    /// Deliver again, from the newest frame.
    Resume,
    /// Reopen every transport of a failed feed from the same content.
    Retry,
}

/// The answer to `Open` for a live stream; the first message of its socket.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct LiveOpenedWire {
    /// The protocol version, to compare with `PLAYBACK_PROTOCOL`.
    pub protocol: u32,
    /// The one lane: the overlay's, or a plain lane when the video has none. Its position in lane messages is 0.
    pub lane: LaneInfo,
    /// What the lane starts with.
    pub lane_defaults: LaneDefaultsWire,
    /// The catalogs the lane may draw with, built-ins first and Standard first among them.
    pub catalogs: Vec<CatalogInfoWire>,
}

/// Where the feeds stand. Sent when any of it changes.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveStatusWire {
    /// The video.
    pub video: LiveFeedStatus,
    /// The overlay; `null` when the video has none.
    pub overlay: Option<LiveFeedStatus>,
    /// Whether the overlay's decoder is behind: its queue overflowed and holds until it drains. The Events list gets a
    /// `LiveGap` for it.
    pub behind: bool,
    /// Whether the overlay is up but has delivered nothing on this connection yet; frames do not wait for it meanwhile.
    pub waiting: bool,
}

/// One change to object identity, as a line of the Events list.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveEventWire {
    /// Which kind of change.
    pub kind: OperationKind,
    /// A one-line description such as `Rename 12 → 57`.
    pub label: String,
    /// The objects involved: the ones changed first, then the ones they become.
    pub entities: Vec<String>,
}

impl From<&Event> for LiveEventWire {
    fn from(event: &Event) -> Self {
        Self {
            kind: event.kind(),
            label: event.label(),
            entities: event
                .involved_entity_ids()
                .map(|id| id.as_str().to_owned())
                .collect(),
        }
    }
}

/// The events of one sample.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveSampleEventsWire {
    /// The sample's time on the video clock, in microseconds.
    #[ts(type = "number")]
    pub time_us: i64,
    /// Its events, in the Scene's order; never empty.
    pub events: Vec<LiveEventWire>,
}

/// Events a released frame made visible: those of every sample not reported before, up to the one it shows.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveEventsWire {
    /// The frame that made them visible, by sequence id.
    #[ts(type = "number")]
    pub frame: u64,
    /// The samples with events, oldest first.
    pub samples: Vec<LiveSampleEventsWire>,
}

/// Where samples were lost: the overlay's decoder was behind and was reset. The Events list shows a marker.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveGapWire {
    /// When the loss began, on the video clock in microseconds.
    #[ts(type = "number")]
    pub time_us: i64,
}

/// A password in a request. `Debug` prints `***`, so a logged request cannot leak it.
#[derive(Clone, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(type = "string")]
pub struct WireSecret(String);

impl fmt::Debug for WireSecret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("***")
    }
}

impl From<WireSecret> for Secret {
    fn from(secret: WireSecret) -> Self {
        Self::new(secret.0)
    }
}

/// The overlay the user chose for a new stream; anything left out takes the configured default. Mirrors
/// `workspace::intake::NewLiveOverlay`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "mode", rename_all = "lowercase")]
#[ts(optional_fields)]
pub enum NewLiveOverlayWire {
    /// Video only.
    None,
    /// Metadata inside the RTSP stream.
    Rtsp {
        /// The decoder id.
        #[serde(default)]
        handler: Option<String>,
    },
    /// An MQTT broker.
    Mqtt {
        /// The decoder id.
        #[serde(default)]
        handler: Option<String>,
        /// The broker host.
        #[serde(default)]
        broker_host: Option<String>,
        /// The broker port, checked against 1 to 65535.
        #[serde(default)]
        #[ts(type = "number", optional)]
        broker_port: Option<i64>,
        /// The broker user.
        #[serde(default)]
        broker_user: Option<String>,
        /// The broker password.
        #[serde(default)]
        #[serde(skip_serializing_if = "Option::is_none")]
        broker_pass: Option<WireSecret>,
        /// The camera's analytics data source.
        #[serde(default)]
        source_key: Option<String>,
    },
    /// A DataHub topic.
    DataHub {
        /// The decoder id.
        #[serde(default)]
        handler: Option<String>,
        /// The topic.
        #[serde(default)]
        topic: Option<String>,
        /// The topic's instance, from 1.
        #[serde(default)]
        #[ts(type = "number", optional)]
        channel: Option<i64>,
    },
}

/// What the user entered to add a live stream. Text that is absent or blank takes its configured default; numbers are
/// checked against their range by the intake, so its message names the field. Mirrors `workspace::intake::NewLive`.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(default)]
#[ts(optional_fields)]
pub struct NewLiveWire {
    /// The name; the host when empty.
    pub label: Option<String>,
    /// The camera host or a `$NAME` reference.
    pub host: Option<String>,
    /// The camera user or a reference.
    pub username: Option<String>,
    /// The camera password or a reference.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub password: Option<WireSecret>,
    /// The web interface's protocol.
    pub protocol: Option<LiveProtocol>,
    /// The web interface's port, 0 to 65535; 0 or absent is the standard one.
    #[ts(type = "number", optional)]
    pub port: Option<i64>,
    /// The video channel, from 1.
    #[ts(type = "number", optional)]
    pub head: Option<i64>,
    /// The RTSP port, 1 to 65535.
    #[ts(type = "number", optional)]
    pub rtsp_port: Option<i64>,
    /// The resolution such as `1280x720`; blank is the camera's own.
    pub resolution: Option<String>,
    /// A stream URL to use instead of the one made from the host.
    pub url: Option<String>,
    /// The overlay; absent is the configured one.
    pub overlay: Option<NewLiveOverlayWire>,
    /// What the form holds for MQTT while another overlay mode is chosen, kept with the stream.
    pub kept_mqtt: Option<KeptMqttWire>,
    /// What the form holds for DataHub while another overlay mode is chosen, kept with the stream.
    pub kept_datahub: Option<KeptDataHubWire>,
    /// The address of a camera the network scan signed in to. The backend then takes the host, protocol, port and
    /// login from the scan, so the login never reaches the client, and the fields above fill in the rest. Only the
    /// session's owner may add such a stream. Never part of a stored recipe.
    pub found: Option<String>,
    /// A live stream this one is another of. The backend fills a blank user and password from that stream's saved
    /// login, so a literal password never reaches the client, and only while the host, protocol and port are the
    /// same as that stream's and the seat is the session's owner. Never part of a stored recipe.
    #[ts(type = "string", optional)]
    pub like: Option<ItemId>,
}

impl From<NewLiveOverlayWire> for intake::NewLiveOverlay {
    fn from(overlay: NewLiveOverlayWire) -> Self {
        match overlay {
            NewLiveOverlayWire::None => Self::None,
            NewLiveOverlayWire::Rtsp { handler } => Self::Rtsp { handler },
            NewLiveOverlayWire::Mqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass,
                source_key,
            } => Self::Mqtt(intake::NewMqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass: broker_pass.map(Secret::from),
                source_key,
            }),
            NewLiveOverlayWire::DataHub {
                handler,
                topic,
                channel,
            } => Self::DataHub(intake::NewDataHub {
                handler,
                topic,
                channel,
            }),
        }
    }
}

/// A stored password as a request may carry it: a `$NAME` reference as it is, an empty one as empty (so a form can tell
/// it from a literal), and nothing for a literal secret, which never leaves the host.
fn reference_only(secret: &Secret) -> Option<WireSecret> {
    if secret.expose().is_empty() {
        return Some(WireSecret::default());
    }
    secret
        .as_reference()
        .map(|reference| WireSecret(reference.to_owned()))
}

impl From<&LiveOverlay> for NewLiveOverlayWire {
    fn from(overlay: &LiveOverlay) -> Self {
        match overlay {
            LiveOverlay::None => Self::None,
            LiveOverlay::Rtsp { handler } => Self::Rtsp {
                handler: Some(handler.clone()),
            },
            LiveOverlay::Mqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass,
                source_key,
            } => Self::Mqtt {
                handler: Some(handler.clone()),
                broker_host: Some(broker_host.clone()),
                broker_port: Some(i64::from(*broker_port)),
                broker_user: Some(broker_user.clone()),
                broker_pass: reference_only(broker_pass),
                source_key: Some(source_key.clone()),
            },
            LiveOverlay::DataHub {
                handler,
                topic,
                channel,
            } => Self::DataHub {
                handler: Some(handler.clone()),
                topic: Some(topic.clone()),
                channel: Some(i64::from(*channel)),
            },
        }
    }
}

/// The request that would make `recipe` again, for a client that may know the host. Text is as stored, so a `$NAME`
/// reference stays one; a literal password is left out. The label is not part of a recipe and stays unset.
impl From<&LiveRecipe> for NewLiveWire {
    fn from(recipe: &LiveRecipe) -> Self {
        let LiveRecipe {
            host,
            username,
            password,
            protocol,
            port,
            head,
            rtsp_port,
            resolution,
            url,
            overlay,
            kept,
        } = recipe;
        Self {
            label: None,
            host: Some(host.clone()),
            username: Some(username.clone()),
            password: reference_only(password),
            protocol: Some((*protocol).into()),
            port: Some(i64::from(*port)),
            head: Some(i64::from(*head)),
            rtsp_port: Some(i64::from(*rtsp_port)),
            resolution: resolution.clone(),
            url: url.clone(),
            overlay: Some(overlay.into()),
            kept_mqtt: kept.mqtt.as_ref().map(Into::into),
            kept_datahub: kept.datahub.as_ref().map(Into::into),
            found: None,
            like: None,
        }
    }
}

impl From<NewLiveWire> for intake::NewLive {
    fn from(new: NewLiveWire) -> Self {
        Self {
            label: new.label,
            host: new.host,
            username: new.username,
            password: new.password.map(Secret::from),
            protocol: new.protocol.map(Protocol::from),
            port: new.port,
            head: new.head,
            rtsp_port: new.rtsp_port,
            resolution: new.resolution,
            url: new.url,
            overlay: new.overlay.map(Into::into),
            kept_mqtt: new.kept_mqtt.map(Into::into),
            kept_datahub: new.kept_datahub.map(Into::into),
        }
    }
}
