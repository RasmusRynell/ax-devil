//! Wire types of recorded events' requests on the playback socket (`ClientMessage::EventQuery`,
//! `ClientMessage::Event`): the events a lane's whole overlay file holds, placed on the video, and one of them with
//! its objects. The answers are `FileBody::Events` and `FileBody::Event`, in `ServerMessage::File`. Live events have
//! no history to ask: they are kept by the client. Both requests are owners only, one per lane, and a newer `request`
//! replaces the older, which is never answered; neither waits for the lane's objects list.

use scene_store::Search;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Asks for rows of a lane's event list: every event the video shows, in frame order, kept by the search and paged.
/// The answer is `File`, with a `FileBody::Events`. A `limit` outside 1 to 200 is a `LaneProblem`.
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

/// Asks for one event of a lane's file with its objects and the events of its frame. The answer is `File`, with a `FileBody::Event`.
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

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<EventQueryWire>();
    types.visit::<EventAskWire>();
}
