//! What the export hands a renderer and what it gets back. The setup types are also the render page's `Setup`
//! message (`api_types::render`): they are defined here once, with their wire form.

use std::sync::Arc;

use bytes::Bytes;
use foundation::{FrameSize, UserError, VideoFrame};
use media::Picture;
use scene_store::{TrackRead, TrackSnapshot};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// The pixel layout a renderer reads back.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(rename = "OutputWire")]
pub enum Output {
    /// Planar YUV 4:2:0, BT.709 limited range, for a video.
    I420,
    /// Packed RGBA, for a still.
    Rgba,
}

/// How a lane looks, frozen when the export starts.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[ts(rename = "LaneStyleWire")]
pub struct LaneStyle {
    /// The lane's name, written on its cell when several lanes share the picture.
    pub label: String,
    /// The indices, in `LaneStatusWire::Ready.filter`, of the class filters switched on.
    pub enabled_classes: Vec<u32>,
    /// How many class filters the file offers: the length of `LaneStatusWire::Ready.filter`.
    pub class_options: u16,
    /// Bits of the catalog's features switched off.
    pub hidden: u32,
    /// One of the catalog's themes; `null` is the catalog's own look.
    pub theme: Option<String>,
    /// The painter's opacity for samples kept by sticky, 0 to 1.
    pub retained_opacity: f32,
    /// The lane's frame filter expression; `null` draws every sample.
    pub filter: Option<String>,
}

/// One lane the renderer draws.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[ts(rename = "RenderLaneWire")]
pub struct RenderLane {
    /// The viewer's id of the lane's source, as in the Sample envelopes; `null` for a plain lane.
    pub source: Option<u16>,
    /// The catalog document text.
    pub catalog: String,
    /// How the lane looks.
    pub style: LaneStyle,
}

/// The job, once, before the first frame.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct RenderSetup {
    /// What to read back.
    pub output: Output,
    /// The size of the video's pictures.
    pub picture: FrameSize,
    /// The largest output the export accepts; a page that wants more fails the setup.
    pub max_output: FrameSize,
    /// The lanes, in the order they tile.
    pub lanes: Vec<RenderLane>,
}

/// A source's file as frozen at the start, which the host encodes Sample Items from.
#[derive(Clone)]
pub struct FrozenSource {
    /// The decoded file.
    pub track: Arc<dyn TrackRead>,
    /// The file as the viewer held it, once its ingest had ended; the sample indices of the job are of it.
    pub snapshot: Arc<TrackSnapshot>,
    /// The source revision the viewer held.
    pub data_revision: u32,
    /// The viewer's id of the source.
    pub source: u16,
}

impl FrozenSource {
    /// The Sample Item of sample `index` of the frozen snapshot.
    pub fn item(&self, index: usize) -> Result<Arc<[u8]>, UserError> {
        self.track.item_in(&self.snapshot, index)
    }
}

/// A sample the renderer does not hold yet.
#[derive(Clone)]
pub struct NewSample {
    /// The source the sample belongs to.
    pub source: FrozenSource,
    /// The sample's index in the file.
    pub index: u32,
}

/// One frame to draw.
#[derive(Clone)]
pub struct RenderJob {
    /// Counts from 0 in submission order; the pixels echo it.
    pub seq: u64,
    /// The video's picture.
    pub frame: VideoFrame,
    /// The samples to send before drawing: those the previous job did not name, each once.
    pub samples: Vec<NewSample>,
    /// The sample each lane draws, as a Frame Map cell read through an `Int32Array`: -1 when nothing belongs to the
    /// frame, -2 when a dense file covers it with nothing.
    pub cells: Vec<i32>,
    /// The `MATCH_*` byte of each lane's cell.
    pub kinds: Vec<u8>,
}

/// A picture read back from the renderer, rows top-down.
#[derive(Clone, Debug)]
pub struct Pixels {
    /// The `seq` of the job it answers.
    pub seq: u64,
    /// The layout of `data`.
    pub output: Output,
    /// Width in pixels.
    pub width: u32,
    /// Height in pixels.
    pub height: u32,
    /// Bytes per row of the first plane.
    pub stride0: u32,
    /// Bytes per row of each chroma plane; 0 for RGBA.
    pub stride1: u32,
    /// The planes back to back: Y, U, V, or the one RGBA plane. Shares the buffer the renderer's message arrived in.
    pub data: Bytes,
}

impl Pixels {
    /// The planes as an encoder picture, checked to be whole.
    pub(crate) fn picture(&self) -> Result<Picture<'_>, UserError> {
        let bad = || crate::fail("the renderer sent a picture whose planes do not fit its size");
        let to = |value: u32| usize::try_from(value).map_err(|_| bad());
        let (width, height) = (to(self.width)?, to(self.height)?);
        let (stride0, stride1) = (to(self.stride0)?, to(self.stride1)?);
        match self.output {
            Output::Rgba => {
                if stride0 < width.saturating_mul(4)
                    || self.data.len() < stride0.saturating_mul(height)
                {
                    return Err(bad());
                }
                Ok(Picture::Rgba {
                    data: &self.data,
                    stride: stride0,
                })
            }
            Output::I420 => {
                let (luma, chroma) = (
                    stride0.saturating_mul(height),
                    stride1.saturating_mul(height / 2),
                );
                if stride0 < width
                    || stride1 < width / 2
                    || self.data.len() < luma.saturating_add(chroma.saturating_mul(2))
                {
                    return Err(bad());
                }
                let (y, rest) = self.data.split_at(luma);
                let (u, v) = rest.split_at(chroma);
                Ok(Picture::I420 {
                    y,
                    u,
                    v,
                    y_stride: stride0,
                    c_stride: stride1,
                })
            }
        }
    }
}

/// How many jobs the export submits before it waits for pixels.
pub const IN_FLIGHT: usize = 2;

/// Draws the overlays of an export. The render host implements it over the hidden render page.
///
/// Calls come from one thread in order: one `setup`, then `submit` and `receive` alternating so that at most
/// [`IN_FLIGHT`] jobs wait; `receive` returns the answers in submission order.
pub trait Renderer {
    /// Prepares the page for `setup` and returns the size of the pictures it will produce.
    fn setup(&mut self, setup: &RenderSetup) -> Result<(u32, u32), UserError>;

    /// Sends the job's samples and picture, then asks for the frame to be drawn.
    fn submit(&mut self, job: RenderJob) -> Result<(), UserError>;

    /// Waits for the pixels of the oldest job not answered yet.
    fn receive(&mut self) -> Result<Pixels, UserError>;
}
