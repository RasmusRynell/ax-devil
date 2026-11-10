//! Wire types of recorded events on the playback socket (`ClientMessage::EventQuery`, `ClientMessage::Event`,
//! `ServerMessage::EventRows`, `ServerMessage::Event`): the events a lane's whole overlay file holds, placed on the
//! video, and one of them with its objects. The rows are `scene_store`'s own types. Live events have no history to
//! ask: they are kept by the client. Both requests are owners only, one per lane, and a newer `request` replaces the
//! older, which is never answered; neither waits for the lane's objects list.

use super::FileScopeUnavailable;
use scene_store::{EventDetail, EventRow, Search};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Asks for rows of a lane's event list: every event the video shows, in frame order, kept by the search and paged.
/// The answer is `EventRows`. A `limit` outside 1 to 200 is a `LaneProblem`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct EventQueryWire {
    /// The lane's position in `Opened.lanes`.
    pub lane: u16,
    /// Names the request in its answer; the client drops answers of older requests.
    pub request: u32,
    /// The search, parsed by the client: fields `kind` and `object`, free text over the label.
    pub query: Search,
    /// The first row to send.
    pub offset: u32,
    /// How many rows to send at most, from 1 to 200.
    pub limit: u32,
    /// An event whose row in the list is wanted whatever the page: the answer's `located`.
    pub locate: Option<u32>,
    /// A frame whose place in the list is wanted: the answer's `frame_row`.
    #[ts(type = "number | null")]
    pub frame: Option<u64>,
}

/// Asks for one event of a lane's file with its objects and the events of its frame. The answer is `Event`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct EventAskWire {
    /// The lane's position in `Opened.lanes`.
    pub lane: u16,
    /// Names the request in its answer; the client drops answers of older requests.
    pub request: u32,
    /// The event's index in the file's events: `EventRow::event`.
    pub event: u32,
    /// The most spans of presence per object, from 0 to 256; 0 sends none.
    pub bins: u16,
}

/// The answer to `EventQuery`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct EventRowsWire {
    /// The lane's position in `Opened.lanes`.
    pub lane: u16,
    /// The request answered.
    pub request: u32,
    /// The rows, or why there are none.
    pub rows: FileEvents,
}

/// The answer to `Event`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct EventWire {
    /// The lane's position in `Opened.lanes`.
    pub lane: u16,
    /// The request answered.
    pub request: u32,
    /// The event from the request.
    pub event: u32,
    /// The event, or why there is none.
    pub detail: FileEvent,
}

/// Rows of a lane's event list, or why there are none.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum FileEvents {
    /// The list was made.
    Found {
        /// The source revision it was listed under; the client drops rows of any other.
        data_revision: u32,
        /// The lane policy revision the events were placed under; the client drops rows of any other.
        policy_revision: u32,
        /// How many events the video shows.
        #[ts(type = "number")]
        total: u64,
        /// How many of them the search matches, which is how many rows there are.
        #[ts(type = "number")]
        matched: u64,
        /// The request's `offset`.
        offset: u32,
        /// Rows `offset` to `offset + rows.len()` of the list, in frame order.
        rows: Vec<EventRow>,
        /// The row of the request's `locate` event; `None` when the request named none or the list does not hold it.
        located: Option<u32>,
        /// How many rows are on frames at or before the request's `frame`, which is where the line for that frame
        /// goes; `None` when the request named none.
        frame_row: Option<u32>,
    },
    /// There are no rows, and why.
    Unavailable {
        /// Why the file cannot be asked.
        unavailable: FileScopeUnavailable,
    },
}

/// One event of a lane's file, or why there is none.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum FileEvent {
    /// The file was asked.
    Found {
        /// The source revision it was made under; the client drops an answer of any other.
        data_revision: u32,
        /// The lane policy revision the events were placed under; the client drops an answer of any other.
        policy_revision: u32,
        /// The event; `None` when the file has no such event or the video does not show it.
        detail: Option<EventDetail>,
    },
    /// There is no event, and why.
    Unavailable {
        /// Why the file cannot be asked.
        unavailable: FileScopeUnavailable,
    },
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<EventQueryWire>();
    types.visit::<EventAskWire>();
    types.visit::<EventRowsWire>();
    types.visit::<EventWire>();
    types.visit::<FileEvents>();
    types.visit::<FileEvent>();
}
