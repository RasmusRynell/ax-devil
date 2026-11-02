//! The hidden render page that draws an export's overlays, and the private socket the backend talks to it over.
//!
//! The host listens on a loopback port, starts Electron on `desktop/render.cjs` (offscreen, no display), and accepts
//! one connection whose first message proves, with a secret given to the page process alone, that the backend
//! started it. Then [`RenderHost::setup`] describes the job, and each frame is a [`RenderHost::submit`] (the new
//! Sample envelopes, the picture, a `Render`) answered by one [`RenderHost::receive`] (the pixels). The protocol is
//! `api_types::render`. Nothing here waits for a frame to be drawn before the next is sent, so a caller may keep a
//! few submitted ahead.
//!
//! The page runs on the GPU first. If the page cannot be set up there, the host starts it once more on software
//! rendering (SwiftShader) and repeats the setup. `AX_DEVIL_RENDER_GRAPHICS=software` starts on SwiftShader at once,
//! for pixels that do not depend on the machine's graphics (the tests) and for a GPU that is known to misbehave.
//! Dropping the host ends the page process.

mod link;
mod page;
mod pixels;
#[cfg(test)]
mod tests;

use api_types::{
    playback::envelope,
    render::{HostMessage, PageMessage, parse_pixels},
};
use export::{Output, Pixels, RenderSetup};
use foundation::{CancelToken, FrameSize, VideoFrame};
use link::{FRAME_TIMEOUT, Link, SETUP_TIMEOUT, connect};
use overlays::SampleItem;
use page::Spawn;
use std::{collections::VecDeque, error::Error, fmt, path::Path};
use tungstenite::Message;

/// Why the render page could not be used, in words for the user.
#[derive(Debug)]
pub struct RenderError {
    reason: String,
}

impl RenderError {
    fn new(reason: impl Into<String>) -> Self {
        Self {
            reason: reason.into(),
        }
    }

    /// What went wrong.
    pub fn reason(&self) -> &str {
        &self.reason
    }
}

impl fmt::Display for RenderError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.reason)
    }
}

impl Error for RenderError {}

/// What the page renders with.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Graphics {
    /// The graphics card, through Vulkan.
    Gpu,
    /// SwiftShader, on the CPU.
    Software,
}

impl Graphics {
    /// What the page starts on: the GPU, unless `AX_DEVIL_RENDER_GRAPHICS` is `software`.
    fn first() -> Self {
        match std::env::var("AX_DEVIL_RENDER_GRAPHICS").as_deref() {
            Ok("software") => Self::Software,
            _ => Self::Gpu,
        }
    }

    /// The Chromium switches that select it.
    fn flags(self) -> &'static [&'static str] {
        match self {
            Self::Gpu => &["--use-angle=vulkan", "--ignore-gpu-blocklist"],
            Self::Software => &["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        }
    }
}

/// One frame to draw.
pub struct Draw<'a> {
    /// Echoed by the pixels that answer it; the answers come in the order the frames were submitted.
    pub seq: u64,
    /// The video frame, of the size `RenderSetup::picture` named.
    pub picture: &'a VideoFrame,
    /// The Sample Items the page does not hold yet; it keeps the ones the previous frame named.
    pub items: &'a [SampleItem],
    /// The sample each lane draws, -1 for none and -2 for a dense file's empty frame.
    pub cells: &'a [i32],
    /// The `MATCH_*` kind of each lane's cell.
    pub kinds: &'a [u8],
}

/// What the page was set up for.
struct Job {
    output: Output,
    picture: FrameSize,
    size: FrameSize,
    lanes: usize,
    renderer: String,
    /// The `seq` of every submitted frame whose pixels have not been received.
    pending: VecDeque<u64>,
}
/// A running render page. One host serves one job.
pub struct RenderHost {
    spawn: Spawn,
    graphics: Graphics,
    cancel: CancelToken,
    link: Link,
    job: Option<Job>,
}

impl RenderHost {
    /// Starts the page from the desktop folder `desktop` on the GPU and returns when it has connected and proved it
    /// is ours. Every wait on the page, here and in the calls below, fails soon after `cancel` is cancelled and
    /// after a fixed time without an answer, whatever the page sends meanwhile.
    pub fn start(desktop: &Path, cancel: &CancelToken) -> Result<Self, RenderError> {
        Self::with_spawner(page::electron(desktop)?, Graphics::first(), cancel)
    }

    fn with_spawner(
        mut spawn: Spawn,
        graphics: Graphics,
        cancel: &CancelToken,
    ) -> Result<Self, RenderError> {
        let link = connect(&mut spawn, graphics, cancel)?;
        Ok(Self {
            spawn,
            graphics,
            cancel: cancel.clone(),
            link,
            job: None,
        })
    }

    /// The renderer the page reported, such as the GPU's name; empty before [`RenderHost::setup`].
    pub fn renderer(&self) -> &str {
        self.job.as_ref().map_or("", |job| &job.renderer)
    }

    /// Describes the job and returns the size of the output the page chose, which is within `setup.max_output`.
    /// Takes the one retry on software rendering when the GPU cannot do it.
    pub fn setup(&mut self, setup: RenderSetup) -> Result<FrameSize, RenderError> {
        if self.job.is_some() {
            return Err(RenderError::new("the render page is already set up"));
        }
        let (output, picture, max_output, lanes) = (
            setup.output,
            setup.picture,
            setup.max_output,
            setup.lanes.len(),
        );
        let message = serde_json::to_string(&HostMessage::Setup(setup)).map_err(|error| {
            RenderError::new(format!(
                "Cannot describe the job to the render page: {error}"
            ))
        })?;
        let attempt = |link: &mut Link| -> Result<Job, RenderError> {
            link.send(Message::text(message.as_str()))?;
            let (width, height, renderer) = match link.page_message(SETUP_TIMEOUT)? {
                PageMessage::Ready {
                    width,
                    height,
                    renderer,
                } => (width, height, renderer),
                PageMessage::Failed { reason } => return Err(RenderError::new(reason)),
                PageMessage::Hello { .. } => {
                    return Err(RenderError::new("the render page said Hello twice"));
                }
            };
            if width == 0 || height == 0 {
                return Err(RenderError::new(format!(
                    "the render page chose a {width}x{height} output"
                )));
            }
            if width > max_output.width || height > max_output.height {
                return Err(RenderError::new(format!(
                    "the output would be {width}x{height}; at most {}x{} can be exported",
                    max_output.width, max_output.height
                )));
            }
            let size = FrameSize { width, height };
            let cap = pixels::largest_message(output, size);
            link.socket.set_config(|config| {
                *config = (*config)
                    .max_message_size(Some(cap))
                    .max_frame_size(Some(cap))
            });
            Ok(Job {
                output,
                picture,
                size,
                lanes,
                renderer,
                pending: VecDeque::new(),
            })
        };
        let job = match attempt(&mut self.link) {
            Err(failure) if self.graphics == Graphics::Gpu && !self.cancel.is_cancelled() => {
                tracing::warn!(
                    "The render page cannot start on the GPU ({failure}); trying software rendering"
                );
                self.link.page.kill();
                self.link = connect(&mut self.spawn, Graphics::Software, &self.cancel)?;
                self.graphics = Graphics::Software;
                attempt(&mut self.link)?
            }
            other => other?,
        };
        tracing::info!("Rendering with {}", job.renderer);
        let size = job.size;
        self.job = Some(job);
        Ok(size)
    }

    /// Sends one frame to be drawn. Returns when the messages are written, not when the frame is drawn.
    pub fn submit(&mut self, draw: &Draw<'_>) -> Result<(), RenderError> {
        let job = self
            .job
            .as_mut()
            .ok_or_else(|| RenderError::new("the render page is not set up"))?;
        let layout = draw.picture.layout();
        if (layout.width, layout.height) != (job.picture.width, job.picture.height) {
            return Err(RenderError::new(format!(
                "a {}x{} picture was submitted to a job of {}x{} pictures",
                layout.width, layout.height, job.picture.width, job.picture.height
            )));
        }
        if draw.cells.len() != job.lanes || draw.kinds.len() != job.lanes {
            return Err(RenderError::new(format!(
                "{} cells and {} kinds were submitted to a job of {} lanes",
                draw.cells.len(),
                draw.kinds.len(),
                job.lanes
            )));
        }
        for item in draw.items {
            self.link.send(Message::Binary(envelope::sample(item)))?;
        }
        self.link
            .send(Message::Binary(envelope::video_frame(draw.picture, false)))?;
        let render = HostMessage::Render {
            seq: draw.seq,
            cells: draw.cells.to_vec(),
            kinds: draw.kinds.to_vec(),
        };
        let text = serde_json::to_string(&render).map_err(|error| {
            RenderError::new(format!(
                "Cannot describe a frame to the render page: {error}"
            ))
        })?;
        self.link.send(Message::text(text))?;
        job.pending.push_back(draw.seq);
        Ok(())
    }

    /// Waits for the pixels of the oldest submitted frame that has none yet.
    pub fn receive(&mut self) -> Result<Pixels, RenderError> {
        let job = self
            .job
            .as_mut()
            .ok_or_else(|| RenderError::new("the render page is not set up"))?;
        let expected = job
            .pending
            .pop_front()
            .ok_or_else(|| RenderError::new("no frame is waiting to be drawn"))?;
        loop {
            match self.link.next(FRAME_TIMEOUT)? {
                Message::Binary(message) => {
                    let pixels = parse_pixels(&message).ok_or_else(|| {
                        RenderError::new(
                            "the render page sent a picture that is not a Pixels message",
                        )
                    })?;
                    pixels::check(&pixels, (job.output, job.size), expected)
                        .map_err(RenderError::new)?;
                    return Ok(pixels);
                }
                Message::Text(text) => {
                    return Err(match serde_json::from_str::<PageMessage>(&text) {
                        Ok(PageMessage::Failed { reason }) => RenderError::new(reason),
                        _ => RenderError::new(
                            "the render page sent a message the host did not expect",
                        ),
                    });
                }
                _ => {}
            }
        }
    }
}
