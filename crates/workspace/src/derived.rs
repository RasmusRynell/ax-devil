//! Lanes computed by a handler from other lanes of an entry.

use crate::content::{Content, LaneSource, OverlayRef, check_unique_lanes};
use foundation::{ItemId, UserError};
use handler_api::Settings;
use std::collections::BTreeMap;

/// A lane computed by a handler from other lanes of each entry, and optionally its video. Item level, by slot, so a
/// video item and every entry of a playlist share one rule.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DerivedLane {
    /// The lane's slot, unique among the item's derived lanes and its entries' own lanes.
    pub slot: String,
    /// What the user reads on the lane.
    pub label: String,
    /// The decoder handler id that computes it.
    pub handler: String,
    /// Options handed to the handler.
    pub options: Settings,
    /// The input lanes: the key the handler declares each under, and the slot of the lane that feeds it. A slot is a
    /// lane of the entry or an earlier derived lane, so a cycle cannot be written.
    pub lanes: BTreeMap<String, String>,
    /// Whether the handler also reads the entry's video.
    pub video: bool,
}

/// Appends to each entry of `content`, after its own lanes, one lane per derived lane whose input slots the entry has,
/// in order, so a later one may read an earlier one. Each input lane is embedded, so opening needs no lookup. A live
/// video has no entries. Fails like [`check_unique_lanes`] when a derived lane repeats a name of the entry.
pub(crate) fn append_derived(
    item: &ItemId,
    content: &mut Content,
    derived: &[DerivedLane],
) -> Result<(), UserError> {
    let entries = match content {
        Content::SeekableVideo(video) => std::slice::from_mut(&mut video.entry),
        Content::Playlist(playlist) => playlist.entries.as_mut_slice(),
        Content::LiveVideo(_) => return Ok(()),
    };
    for entry in entries {
        for lane in derived {
            let inputs: Option<Vec<(String, OverlayRef)>> = lane
                .lanes
                .iter()
                .map(|(key, slot)| Some((key.clone(), entry.lane(slot)?.clone())))
                .collect();
            let Some(lanes) = inputs else { continue };
            entry.overlays.push(OverlayRef {
                label: lane.label.clone(),
                slot: lane.slot.clone(),
                source: LaneSource::Derived {
                    lanes,
                    video: lane.video.then(|| entry.video.clone()),
                },
                handler: lane.handler.clone(),
                options: lane.options.clone(),
                metadata: Default::default(),
            });
        }
        if !derived.is_empty() {
            check_unique_lanes(item, entry)?;
        }
    }
    Ok(())
}
