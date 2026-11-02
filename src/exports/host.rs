//! The render host as the export's renderer.

use crate::{
    desktop,
    render_host::{Draw, RenderError, RenderHost},
};
use export::{Pixels, RenderJob, RenderSetup, Renderer};
use foundation::{CancelToken, ErrorCode, UserError};
use overlays::SampleItem;

/// A renderer on a fresh Electron render page from the desktop folder.
pub(super) fn electron(cancel: &CancelToken) -> Result<Box<dyn Renderer>, UserError> {
    let folder = desktop::folder().map_err(|error| {
        UserError::new(
            ErrorCode::Internal,
            format!("Cannot find the desktop folder: {error}"),
        )
    })?;
    let host = RenderHost::start(&folder, cancel).map_err(failed)?;
    Ok(Box::new(HostRenderer(host)))
}

/// The page's trouble as the user's: the reason says what to do about it.
fn failed(error: RenderError) -> UserError {
    UserError::new(ErrorCode::Internal, error.reason())
}

/// A [`RenderHost`] that draws an export.
struct HostRenderer(RenderHost);

impl Renderer for HostRenderer {
    fn setup(&mut self, setup: &RenderSetup) -> Result<(u32, u32), UserError> {
        let size = self.0.setup(setup.clone()).map_err(failed)?;
        Ok((size.width, size.height))
    }

    fn submit(&mut self, job: RenderJob) -> Result<(), UserError> {
        let mut items = Vec::with_capacity(job.samples.len());
        for new in &job.samples {
            let index = usize::try_from(new.index).unwrap_or(usize::MAX);
            items.push(SampleItem {
                source: new.source.source,
                data_revision: new.source.data_revision,
                sample: u64::from(new.index),
                frame: 0,
                serial: 0,
                item: new.source.item(index)?,
            });
        }
        self.0
            .submit(&Draw {
                seq: job.seq,
                picture: &job.frame,
                items: &items,
                cells: &job.cells,
                kinds: &job.kinds,
            })
            .map_err(failed)
    }

    fn receive(&mut self) -> Result<Pixels, UserError> {
        self.0.receive().map_err(failed)
    }
}
