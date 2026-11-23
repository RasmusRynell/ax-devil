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

/// Finds the nearest event equal in kind, label and ordered entity ids after a file replacement.
/// Uses the same File scope authority and answer as `Event`; `event` echoes the prior index.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct FindEventWire {
    /// The lane's position.
    pub lane: u16,
    /// The request id.
    pub request: u32,
    /// The prior index, echoed in the answer.
    pub event: u32,
    /// Server-issued identity; fixed size regardless of event content.
    pub anchor: scene_store::EventAnchor,
    /// The previously shown frame.
    #[ts(type = "number")]
    pub frame: u64,
    /// At most 256 presence bins per object.
    pub bins: u16,
}

// No heap-owning fields: the find request remains bounded independently of event content.
const _: () = {
    assert!(size_of::<FindEventWire>() <= 64);
    assert!(!std::mem::needs_drop::<FindEventWire>());
};

#[cfg(test)]
mod tests {
    use crate::playback::ClientMessage;

    use super::FindEventWire;

    #[test]
    fn finding_an_event_has_a_fixed_small_request_even_for_a_large_id() {
        let ask = ClientMessage::FindEvent(FindEventWire {
            lane: u16::MAX,
            request: u32::MAX,
            event: u32::MAX,
            anchor: [255; 32],
            frame: u64::MAX,
            bins: 256,
        });
        assert!(serde_json::to_vec(&ask).unwrap().len() < 512);
    }
}
