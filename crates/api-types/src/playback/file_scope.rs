//! Wire types of File scope queries on the playback socket (`ClientMessage::FileQuery`, `ServerMessage::FileRows`,
//! `ServerMessage::FileScope`): the objects a whole overlay file holds, as one lane shows them. The query and its
//! rows are `scene_store`'s own types. `ClientMessage::Appearances` and `ServerMessage::Appearances` ask where one
//! object is.

use crate::ErrorWire;
use scene_store::EventRow;
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

/// Where one object is on the video, the answer to `ClientMessage::Appearances`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum FileAppearances {
    /// The spans were made.
    Found {
        /// The source revision they were made under; the client drops spans of any other.
        data_revision: u32,
        /// The lane policy revision they were placed under; the client drops spans of any other.
        policy_revision: u32,
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
    /// There are no spans, and why.
    Unavailable {
        /// Why the file cannot be asked.
        unavailable: FileScopeUnavailable,
    },
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<FileScopeUnavailable>();
    types.visit::<Located>();
    types.visit::<FileAppearances>();
}
