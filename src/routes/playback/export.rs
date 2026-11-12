//! A viewer's export: the owner asks for one with the lanes as it holds them, and the backend either refuses it or
//! freezes those lanes, the catalogs it served and the video's frames into an `export::ExportSpec` for the backend's
//! export service. Progress and the outcome come back as `Export` notices, which name no frame and are never gated.
//!
//! A refusal is a `Failed` notice for the job asked for; it does not end the session. A viewer has one job, and the
//! job ends with the viewer.

use super::{Outgoing, catalogs::CatalogFeed, text};
use crate::{
    exports::{ExportJob, ExportService, ExportUpdate},
    video_sources::{VideoSources, video_location},
};
use api_types::{
    Audience,
    playback::{
        ExportFormatWire, ExportLaneWire, ExportQualityWire, ExportStateWire, ServerMessage,
    },
    project,
};
use export::{ExportLane, ExportSpec, FrozenSource};
use foundation::{ErrorCode, UserError};
use media::{EncodeFormat, Quality};
use overlays::ViewerOverlays;
use std::{
    collections::{BTreeSet, VecDeque},
    path::PathBuf,
    sync::Arc,
};
use workspace::{Entry, VideoRef};

/// What an `Export` message asked for.
pub(super) struct Request {
    pub(super) job: u32,
    pub(super) lanes: Vec<ExportLaneWire>,
    pub(super) first: u64,
    pub(super) last: u64,
    pub(super) format: ExportFormatWire,
    pub(super) path: String,
}

/// A viewer's export: at most one job, and the refusals not yet told.
pub(super) struct Exports {
    service: Arc<ExportService>,
    videos: Arc<VideoSources>,
    video: VideoRef,
    /// The overlay file of each lane; empty for an entry without overlays.
    files: Vec<PathBuf>,
    audience: Audience,
    running: Option<(u32, ExportJob)>,
    refused: VecDeque<(u32, UserError)>,
}

impl Exports {
    /// The exports of a viewer of `entry`.
    pub(super) fn new(
        entry: &Entry,
        audience: Audience,
        service: &Arc<ExportService>,
        videos: &Arc<VideoSources>,
    ) -> Self {
        Self {
            service: Arc::clone(service),
            videos: Arc::clone(videos),
            video: entry.video.clone(),
            files: entry
                .overlays
                .iter()
                .map(|overlay| overlay.path.clone())
                .collect(),
            audience,
            running: None,
            refused: VecDeque::new(),
        }
    }

    /// Starts the export asked for, or queues the reason it was refused. `last_frame` is the video's last frame.
    pub(super) fn start(
        &mut self,
        request: Request,
        overlays: &ViewerOverlays,
        catalogs: &CatalogFeed,
        last_frame: u64,
    ) {
        let job = request.job;
        match self.begin(request, overlays, catalogs, last_frame) {
            Ok(running) => self.running = Some((job, running)),
            Err(error) => self.refused.push_back((job, error)),
        }
    }

    /// Cancels `job` if it is the one running; its outcome arrives as a notice.
    pub(super) fn cancel(&self, job: u32) {
        if let Some((running, export)) = &self.running
            && *running == job
        {
            export.cancel();
        }
    }

    /// The next notice: refusals first, then the running job's progress and outcome. Waits while there is none.
    pub(super) async fn next(&mut self) -> Outgoing {
        if let Some((job, error)) = self.refused.pop_front() {
            return notice(job, ExportUpdate::Failed(error), self.audience);
        }
        let Some((job, export)) = self.running.as_mut() else {
            return std::future::pending().await;
        };
        let job = *job;
        let update = export.next().await;
        if update.is_final() {
            self.running = None;
        }
        notice(job, update, self.audience)
    }

    fn begin(
        &self,
        request: Request,
        overlays: &ViewerOverlays,
        catalogs: &CatalogFeed,
        last_frame: u64,
    ) -> Result<ExportJob, UserError> {
        if !self.audience.host_details {
            return Err(refuse("Only the owner of a session can export"));
        }
        if self.running.is_some() {
            return Err(refuse("This viewer is already exporting"));
        }
        let Request {
            first,
            last,
            format,
            lanes,
            path,
            ..
        } = request;
        if first > last || last > last_frame {
            return Err(refuse(format!(
                "Frames {first} to {last} are not in the video, which has {}",
                last_frame + 1
            )));
        }
        if format == ExportFormatWire::Png && first != last {
            return Err(refuse("A still is one frame"));
        }
        let snapshot = overlays.snapshot();
        let mut inputs = vec![video_location(&self.video).as_path().to_path_buf()];
        let mut chosen = BTreeSet::new();
        let mut frozen = Vec::with_capacity(lanes.len());
        for wire in lanes {
            let number = wire.lane;
            let lane = snapshot
                .get(usize::from(number))
                .ok_or_else(|| refuse(format!("The video has no lane {number}")))?;
            if !chosen.insert(number) {
                return Err(refuse(format!("Lane {number} is named twice")));
            }
            if wire.policy_revision != lane.policy_revision {
                return Err(refuse(format!(
                    "Lane {number}'s matching changed; export again"
                )));
            }
            let source = match &lane.source {
                Some(source) if wire.data_revision != Some(source.data_revision) => {
                    return Err(refuse(format!(
                        "Lane {number}'s data changed; export again"
                    )));
                }
                Some(source) if source.is_reading() => {
                    return Err(refuse(format!(
                        "Lane {number}'s overlay is still being read. Try again when it has finished."
                    )));
                }
                Some(source) => {
                    inputs.extend(self.files.get(usize::from(number)).cloned());
                    Some(FrozenSource {
                        track: Arc::clone(&source.track),
                        data_revision: source.data_revision,
                        snapshot: Arc::clone(&source.snapshot),
                        source: source.source,
                    })
                }
                None => None,
            };
            let catalog = catalogs
                .kept(&wire.catalog.id, wire.catalog.revision)
                .ok_or_else(|| {
                    refuse(format!(
                        "The viewer was not served revision {} of the catalog {}",
                        wire.catalog.revision, wire.catalog.id
                    ))
                })?;
            frozen.push(ExportLane {
                source,
                policy: lane.matching,
                catalog: catalog.to_string(),
                style: wire.style,
            });
        }
        if frozen.is_empty() {
            return Err(refuse("There is no lane to export"));
        }
        let (video, index) = self
            .videos
            .open_input(&self.video)
            .ok_or_else(|| UserError::new(ErrorCode::Internal, "The video is no longer open"))?;
        self.service.start(ExportSpec {
            video,
            index,
            frames: first..=last,
            lanes: frozen,
            format: match format {
                ExportFormatWire::Mp4 { quality } => EncodeFormat::Mp4 {
                    quality: match quality {
                        ExportQualityWire::Lossless => Quality::Lossless,
                        ExportQualityWire::High => Quality::High,
                        ExportQualityWire::Balanced => Quality::Balanced,
                        ExportQualityWire::Small => Quality::Small,
                    },
                },
                ExportFormatWire::Png => EncodeFormat::Png,
            },
            destination: PathBuf::from(path),
            inputs,
        })
    }
}

/// The message about `job`. It names no frame, so no authority holds it back.
fn notice(job: u32, update: ExportUpdate, audience: Audience) -> Outgoing {
    let state = match update {
        ExportUpdate::Running { done, total } => ExportStateWire::Running { done, total },
        ExportUpdate::Done => ExportStateWire::Done,
        ExportUpdate::Cancelled => ExportStateWire::Cancelled,
        ExportUpdate::Failed(error) => ExportStateWire::Failed(project(&error, audience)),
    };
    text(&ServerMessage::Export { job, state }).into()
}

fn refuse(reason: impl Into<String>) -> UserError {
    UserError::new(ErrorCode::ProtocolViolation, reason)
}

#[cfg(test)]
mod tests {
    mod http_catalog;
    use super::*;

    #[test]
    fn progress_and_outcome_notices_name_no_frame_so_the_gate_never_holds_them_back() {
        let error = UserError::new(ErrorCode::Internal, "no");
        for update in [
            ExportUpdate::Running { done: 1, total: 2 },
            ExportUpdate::Done,
            ExportUpdate::Cancelled,
            ExportUpdate::Failed(error),
        ] {
            let outgoing = notice(1, update, Audience::HOST);
            assert!(outgoing.frames.is_none() && outgoing.data.is_none());
        }
    }
}
