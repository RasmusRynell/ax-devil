//! Wire types of File scope requests on the playback socket: the objects and events a lane's whole overlay file holds,
//! as the lane shows them. The requests are `ClientMessage::FileQuery`, `Appearances`, `EventQuery` and `Event`; every
//! answer is one `ServerMessage::File` holding a [`FileAnswer`], which the client matches to its request by lane and
//! request id. The query, rows and event types are `scene_store`'s own.

use crate::ErrorWire;
use scene_store::{EventDetail, EventRow, Row};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Why a File scope query has no rows.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum FileScopeUnavailable {
    /// The file is not loaded or still being read; ask again when the lane's status says it is complete or incomplete.
    Reading,
    /// The viewer follows a presenter, who alone sees the file beyond the frames shown.
    Following,
    /// The file has more objects than a list can hold.
    TooLarge,
    /// The file's objects could not be listed, and why.
    Failed(ErrorWire),
    /// The lane draws no overlay file.
    PlainLane,
}

/// Where an object is in a File scope list, the answer to `ClientMessage::FileQuery`'s `locate`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct Located {
    /// The object's id from the request.
    pub id: String,
    /// The index of its row in the whole list, whichever page was asked; `None` when the list does not hold it.
    pub row: Option<u32>,
}

/// The answer to a File scope request (`FileQuery`, `Appearances`, `EventQuery` or `Event`): what the file holds, or
/// why it cannot be asked. The client matches it to its request by `ServerMessage::File`'s `lane` and `request`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum FileAnswer {
    /// The request was answered.
    Found {
        /// The source revision the answer was made under; the client drops an answer of any other.
        data_revision: u32,
        /// The lane policy revision the answer was placed under; the client drops an answer of any other.
        policy_revision: u32,
        /// What was found; its kind is the request's.
        body: FileBody,
    },
    /// There is no answer, and why.
    Unavailable {
        /// Why the file cannot be asked.
        unavailable: FileScopeUnavailable,
    },
}

/// What a found File scope answer holds, by the kind of request it answers.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "kind")]
pub enum FileBody {
    /// Rows of the objects list, the answer to `FileQuery`.
    Objects {
        /// How many objects the video shows that the class toggles keep.
        #[ts(type = "number")]
        total: u64,
        /// How many of them the search matches, which is how many rows there are.
        #[ts(type = "number")]
        matched: u64,
        /// The request's `offset`.
        offset: u32,
        /// Rows `offset` to `offset + rows.len()` of the list.
        rows: Vec<Row>,
        /// Where the request's `locate` object is in the list; `None` when the request named none.
        located: Option<Located>,
    },
    /// Where one object is on the video, the answer to `Appearances`.
    Appearances {
        /// The object's id from the request.
        entity: String,
        /// The frames the video shows the object on, as `[first, last]` spans of consecutive frames, ascending, with a
        /// frame between two. Empty when the file has no such object or no frame shows it.
        #[ts(type = "Array<[number, number]>")]
        spans: Vec<[u64; 2]>,
        /// Whether the object has more spans than are sent: these are its first ones.
        truncated: bool,
        /// The events that involve the object, in frame order.
        events: Vec<EventRow>,
        /// Whether the object has more events than are sent: these are its first ones.
        events_truncated: bool,
    },
    /// Rows of the event list, the answer to `EventQuery`.
    Events {
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
    /// One event, the answer to `Event`.
    Event {
        /// The event from the request.
        event: u32,
        /// The event; `None` when the file has no such event or the video does not show it.
        detail: Option<EventDetail>,
    },
}
