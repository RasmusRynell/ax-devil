//! What a playback session knows of its viewer and of the entry it opened.

use super::wire::Wire;
use crate::{exports::ExportService, prepare::Preparer, video_sources::VideoSources};
use api_types::Audience;
use catalogs::Library;
use foundation::{FrameRate, FrameSize};
use media::{Cadence, PtsTiming};
use overlays::OverlayService;
use scene::matching::MatchPolicy;
use sessions::SessionId;
use settings::Settings;
use std::{collections::BTreeMap, sync::Arc};
use workspace::{Content, Entry};

/// What the stream knows of the viewer and of the entry it opened.
pub(super) struct Setup<'a> {
    /// The entry: its video, which the session plays, and its overlays, one lane each.
    pub(super) entry: &'a Entry,
    /// How the workspace says each lane matches its records to frames; `None` is the default.
    pub(super) matching: &'a BTreeMap<String, MatchPolicy>,
    /// The frame the viewer opened on.
    pub(super) start_frame: Option<u64>,
    /// The most bytes of Sample Items the viewer holds at once.
    pub(super) data_bytes: u64,
    /// What the viewer may learn from an error, a warning or a lane problem.
    pub(super) audience: Audience,
    /// The Session the viewer is in, which scopes the user catalogs a Guest is shown.
    pub(super) session: SessionId,
    /// The size of the decoded pictures.
    pub(super) size: FrameSize,
    /// The form the viewer is sent frames in.
    pub(super) wire: Wire,
    /// The video's warnings, which name the host's files.
    pub(super) warnings: Vec<String>,
    /// How the video's frame times come from its timestamps; `None` for an image sequence.
    pub(super) pts: Option<PtsTiming>,
    /// How evenly the video's frames are spaced; `None` for a one-frame video.
    pub(super) cadence: Option<Cadence>,
    /// The rate the video's stream declares; `None` when it declares none.
    pub(super) frame_rate: Option<FrameRate>,
    /// The catalogs a lane may draw with, which the viewer asks for by id.
    pub(super) library: &'a Library,
    /// Opens the lanes' overlays.
    pub(super) overlays: &'a OverlayService,
    /// Runs the viewer's export.
    pub(super) exports: &'a Arc<ExportService>,
    /// The open videos, which an export reads from.
    pub(super) videos: &'a Arc<VideoSources>,
    /// The Content the entry belongs to, whose other entries the viewer may ask to have prepared.
    pub(super) content: &'a Content,
    /// Readies the entry the viewer asks for.
    pub(super) preparer: &'a Preparer,
    /// Says whether it is allowed.
    pub(super) settings: &'a Settings,
}
