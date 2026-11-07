//! What a viewer reads of a track, whether it is a decoded file or a live feed.

use std::sync::{Arc, Weak};

use foundation::{UserError, Warning};
use handler_api::Form;
use overlay_sync::MatchBasis;
use scene::{
    Coverage, Fields, FilterPolicy, Scene, SceneTime, TimeSlice,
    serial::columns::encode_sample_item,
};

use crate::{History, HistoryUnavailable, TrackSnapshot};

/// A source of samples that can be read while it grows: a [`Track`](crate::Track) over an overlay file, or a
/// [`LiveTrack`](crate::LiveTrack) over a feed.
pub trait TrackRead: Send + Sync {
    /// The bases a lane may match the samples by.
    fn bases(&self) -> &[MatchBasis];

    /// The basis a lane starts with.
    fn default_basis(&self) -> MatchBasis;

    /// The options the source's decoder takes.
    fn options(&self) -> &Form;

    /// What is published now: the keys, which frames they decide and how far the ingest has got.
    fn snapshot(&self) -> Arc<TrackSnapshot>;

    /// Calls `listener` after every publication, on the thread that publishes, with no lock held. Only a weak
    /// reference is kept: dropping the listener's `Arc` removes it.
    fn on_change(&self, listener: Weak<dyn Fn() + Send + Sync>);

    /// The file history, once the ingest has ended: the objects the file holds, their classes, events and runs of
    /// samples. Shared while any caller holds it, so hold it across the pages of one query.
    fn history(&self) -> Result<Arc<History>, HistoryUnavailable>;

    /// What the source says about frames it has no Scene for.
    fn coverage(&self) -> Coverage;

    /// The class filters the source's content calls for.
    fn filter(&self) -> &FilterPolicy;

    /// What the source says about itself.
    fn metadata(&self) -> &Fields;

    /// The Scene of sample `index` of `snapshot`, a snapshot of this track: read by the numbering the snapshot holds,
    /// so a track that has numbered its samples anew since does not change which sample this is.
    fn scene_in(&self, snapshot: &TrackSnapshot, index: usize) -> Result<Arc<Scene>, UserError>;

    /// The encoded Sample Item of sample `index` of `snapshot`, a snapshot of this track, under the track's class
    /// filters and shared by every viewer of the track.
    fn item_in(&self, snapshot: &TrackSnapshot, index: usize) -> Result<Arc<[u8]>, UserError>;

    /// The Sample Item of a Scene with no entities under the source's class filters: what stands in for a sample
    /// that cannot be read.
    fn empty_item(&self) -> Arc<[u8]> {
        let scene = Scene::new(TimeSlice::at(SceneTime::Frame(0)));
        encode_sample_item(&scene, self.filter())
            .unwrap_or_else(|error| {
                tracing::error!("cannot encode an empty Sample Item: {error}");
                Vec::new()
            })
            .into()
    }

    /// Problems the decoder and the store reported so far.
    fn warnings(&self) -> Arc<[Warning]> {
        self.snapshot().warnings.clone()
    }

    /// The Scene of sample `index` of the current snapshot; see [`scene_in`](Self::scene_in).
    fn scene(&self, index: usize) -> Result<Arc<Scene>, UserError> {
        self.scene_in(&self.snapshot(), index)
    }

    /// The encoded Sample Item of sample `index` of the current snapshot; see [`item_in`](Self::item_in).
    fn item(&self, index: usize) -> Result<Arc<[u8]>, UserError> {
        self.item_in(&self.snapshot(), index)
    }
}
