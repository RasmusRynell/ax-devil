//! Wire types of File scope queries on the playback socket (`ClientMessage::FileQuery`, `ServerMessage::FileRows`,
//! `ServerMessage::FileScope`): the objects a whole overlay file holds, as one lane shows them. The query and its
//! rows are `scene_store`'s own types.

use crate::ErrorWire;
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
