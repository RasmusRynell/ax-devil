//! Wire types of a lane: how it selects samples, what it offers and where its overlay load stands.

use crate::ErrorWire;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// What a lane matches a file's records to video frames by.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub enum BasisWire {
    /// By frame number: a time-keyed file's record k belongs to frame k.
    Frame,
    /// By the records' timestamps.
    Time,
}

/// How a lane selects samples for frames: the part of a lane's settings the backend needs.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LanePolicyWire {
    /// What records are matched to frames by; `null` is the decoder's default. A basis the file does not offer is
    /// reported as a problem and the decoder's default is used.
    pub basis: Option<BasisWire>,
    /// How far back a sample may be under the time basis, in milliseconds; `null` is exact.
    pub tolerance_ms: Option<u32>,
    /// Whether old samples stay on screen.
    pub sticky: StickyWire,
}

/// Keeping the last sample on screen while no sample matches.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct StickyWire {
    /// Whether samples are kept.
    pub enabled: bool,
    /// How old a kept sample may be, in milliseconds, at most 100000; `null` is without limit.
    pub timeout_ms: Option<u32>,
}

/// What every lane starts with.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LaneDefaultsWire {
    /// The catalog id, such as `built-in:standard`.
    pub catalog: String,
    /// The sample selection every lane starts with.
    pub policy: LanePolicyWire,
}

/// One lane of the opened entry.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LaneInfo {
    /// The lane's name.
    pub label: String,
    /// The viewer's id of the source the lane draws; `null` for a plain lane. Lanes on the same file share one.
    pub source: Option<u16>,
    /// How the workspace says the lane matches its records to frames; absent is the defaults. The viewer starts the
    /// lane from it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub policy: Option<LanePolicyWire>,
}

/// One class filter a lane offers.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct FilterOptionInfo {
    /// Stable id, such as `show_car`.
    pub id: String,
    /// What the user reads.
    pub label: String,
    /// Whether the filter starts switched on.
    pub default_enabled: bool,
}

/// A problem that did not stop an overlay from loading.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct WarningInfo {
    /// What happened.
    pub problem: String,
    /// The first reasons.
    pub reasons: Vec<String>,
    /// What the user can expect as a result.
    pub consequence: String,
    /// Whether more reasons exist than are listed.
    pub more_reasons: bool,
}

/// Where a lane's load stands.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "status")]
pub enum LaneStatusWire {
    /// The overlay file is being decoded.
    Loading,
    /// The overlay is ready to draw.
    Ready {
        /// The bases the file's records can be matched by.
        bases: Vec<BasisWire>,
        /// The basis the decoder recommends, one of `bases`.
        default_basis: BasisWire,
        /// The class filters the file's content calls for.
        filter: Vec<FilterOptionInfo>,
        /// Problems the decoder reported.
        warnings: Vec<WarningInfo>,
        /// How many samples the file has.
        #[ts(type = "number")]
        samples: u64,
        /// The source's revision; its Sample Items and the lane's Frame Maps carry it.
        data_revision: u32,
        /// The mean size of a Sample Item in bytes, 0 for an empty file; sizes the viewer's data window.
        mean_sample_bytes: u32,
        /// How far the file has been read. A file still being read repeats this status under the same
        /// `data_revision` as more of it arrives, and under a new one when the numbering of its samples changes;
        /// the samples and Frame Maps held stay valid for an equal revision.
        ingest: IngestWire,
    },
    /// The overlay could not be loaded, and why.
    Failed(ErrorWire),
}

/// How far an overlay file has been read.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum IngestWire {
    /// More of the file is still coming; Frame Map cells arrive for the frames its samples have decided so far.
    Reading,
    /// The whole file is read.
    Complete,
    /// The reader stopped early, and why. What it read stays; frames after it have no overlay.
    Incomplete(ErrorWire),
}
