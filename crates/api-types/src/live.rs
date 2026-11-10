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
use scene::{Event, OperationKind};
use serde::{Deserialize, Serialize};
use ts_rs::TS;
use workspace::LiveOverlay;

mod discovery;
mod glance;
mod kept;
mod new_live;
pub use discovery::*;
pub use glance::*;
pub use kept::*;
pub use new_live::*;

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
    /// The camera does not offer the analytics data source the stream asks for.
    SourceUnavailable,
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

/// Visits the types this module sends to the frontend, and its modules' types.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<LiveOverlayKind>();
    types.visit::<LiveReason>();
    types.visit::<LiveFeedStatus>();
    types.visit::<LiveAction>();
    types.visit::<LiveOpenedWire>();
    types.visit::<LiveStatusWire>();
    types.visit::<LiveEventWire>();
    types.visit::<LiveSampleEventsWire>();
    types.visit::<LiveEventsWire>();
    types.visit::<LiveGapWire>();
    discovery::visit_api_types(types);
    glance::visit_api_types(types);
    kept::visit_api_types(types);
    new_live::visit_api_types(types);
}
