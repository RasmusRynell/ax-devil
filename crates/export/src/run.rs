//! The export loop: decode, select, render, encode.

use std::collections::{BTreeMap, BTreeSet, VecDeque};

use foundation::{CancelToken, ErrorCode, FrameSize, Interrupt, UserError, VideoFrame};
use media::{Decoder, EncodeFormat, Encoder, VideoInput};
use overlays::{FIRST_NON_SAMPLE, cell};

use crate::{
    ExportError, ExportSpec, FrozenSource, IN_FLIGHT, NewSample, Output, RenderJob, RenderLane,
    RenderSetup, Renderer, destination, fail,
};

/// The largest picture an export makes.
const MAX_OUTPUT: FrameSize = FrameSize {
    width: 3840,
    height: 2160,
};
/// FFmpeg threads of the export's decoder.
const DECODE_THREADS: usize = 2;
/// How long a frame lasts, in microseconds, when the timeline has no neighbour to tell and the video declares no
/// rate: a video file's index keeps only presentation times.
const LONE_FRAME_US: i64 = 33_333;

/// Runs `spec`: writes the destination, or returns why it did not, leaving the destination as it was.
///
/// `progress(done, total)` is called with 0 at the start and after each frame is written. `cancel` is checked between
/// frames; a cancelled export is [`ExportError::Cancelled`].
pub fn run(
    spec: &ExportSpec,
    renderer: &mut dyn Renderer,
    cancel: &CancelToken,
    progress: &mut dyn FnMut(u64, u64),
) -> Result<(), ExportError> {
    cancel.check()?;
    check_job(spec)?;
    destination::check(spec)?;
    // A renderer stopped by the cancel fails with whatever it saw; the caller asked for the stop.
    encode(spec, renderer, cancel, progress).map_err(|error| {
        if cancel.is_cancelled() {
            ExportError::Cancelled
        } else {
            error
        }
    })
}

/// Refuses a range or lanes the job cannot draw.
fn check_job(spec: &ExportSpec) -> Result<(), UserError> {
    let (first, last) = (*spec.frames.start(), *spec.frames.end());
    let refuse = |reason: String| UserError::new(ErrorCode::InvalidPath, reason);
    if first > last || last >= spec.index.timeline().len() {
        return Err(refuse(format!(
            "Frames {first} to {last} are not in the video, which has {}",
            spec.index.timeline().len()
        )));
    }
    if spec.format == EncodeFormat::Png && first != last {
        return Err(refuse("A still is one frame".into()));
    }
    if spec.lanes.is_empty() {
        return Err(refuse("There is no lane to export".into()));
    }
    Ok(())
}

/// Draws and encodes the range, then puts the file in place.
fn encode(
    spec: &ExportSpec,
    renderer: &mut dyn Renderer,
    cancel: &CancelToken,
    progress: &mut dyn FnMut(u64, u64),
) -> Result<(), ExportError> {
    let (first, last) = (*spec.frames.start(), *spec.frames.end());
    let info = spec.index.info();
    let output = match spec.format {
        EncodeFormat::Mp4 { .. } => Output::I420,
        EncodeFormat::Png => Output::Rgba,
    };
    let (width, height) = renderer.setup(&RenderSetup {
        output,
        picture: FrameSize {
            width: info.width,
            height: info.height,
        },
        max_output: MAX_OUTPUT,
        lanes: spec
            .lanes
            .iter()
            .map(|lane| RenderLane {
                source: lane.source.as_ref().map(|source| source.source),
                catalog: lane.catalog.clone(),
                style: lane.style.clone(),
            })
            .collect(),
    })?;
    if width == 0 || height == 0 || width > MAX_OUTPUT.width || height > MAX_OUTPUT.height {
        return Err(fail(format!(
            "The picture would be {width}x{height}; the most is {}x{}",
            MAX_OUTPUT.width, MAX_OUTPUT.height
        ))
        .into());
    }
    let mut sink = Sink {
        encoder: Encoder::create(&spec.destination, spec.format, width, height)?,
        output,
        size: (width, height),
        waiting: VecDeque::new(),
        done: 0,
        total: last - first + 1,
    };
    let mut decoder = Decoder::open(
        &spec.video,
        spec.index.clone(),
        DECODE_THREADS,
        cancel.clone(),
    )
    .map_err(|interrupt| match interrupt {
        Interrupt::Cancelled => ExportError::Cancelled,
        Interrupt::Failed(error) => ExportError::Failed(error),
    })?;
    decoder.seek_to_keyframe(spec.index.keyframes().at_or_before(first))?;
    progress(0, sink.total);

    let mut held = BTreeSet::new();
    let mut submitted = 0;
    while submitted < sink.total {
        cancel.check()?;
        let frame = match decoder.next_frame() {
            Ok(Some(frame)) => frame,
            Ok(None) => {
                cancel.check()?;
                return Err(fail(format!("The video ends before frame {last}")).into());
            }
            // Frames before the range are only decoded to reach it.
            Err(failure) if failure.sequence_id < first => continue,
            Err(failure) => {
                let mut error = failure.error;
                error.reason = format!(
                    "Frame {} cannot be read: {}",
                    failure.sequence_id, error.reason
                );
                return Err(error.into());
            }
        };
        if frame.id().sequence_id < first {
            continue;
        }
        let job = job_for(spec, frame, submitted, &mut held)?;
        sink.waiting.push_back(Pending::of(spec, &job));
        renderer.submit(job)?;
        submitted += 1;
        if sink.waiting.len() >= IN_FLIGHT {
            sink.write_next(renderer, progress)?;
        }
    }
    while !sink.waiting.is_empty() {
        cancel.check()?;
        sink.write_next(renderer, progress)?;
    }
    cancel.check()?;
    sink.encoder.finish()?;
    Ok(())
}

/// The job for `frame`: each lane's cell, and the samples `held` (those of the previous job) lacks. `held` becomes
/// the samples this job names.
fn job_for(
    spec: &ExportSpec,
    frame: VideoFrame,
    seq: u64,
    held: &mut BTreeSet<(u16, u32)>,
) -> Result<RenderJob, UserError> {
    let at = frame.id().sequence_id;
    let timeline = spec.index.timeline();
    let mut cells = Vec::with_capacity(spec.lanes.len());
    let mut kinds = Vec::with_capacity(spec.lanes.len());
    let mut named: BTreeMap<(u16, u32), &FrozenSource> = BTreeMap::new();
    for lane in &spec.lanes {
        let (value, kind) = match &lane.source {
            Some(source) => cell(&source.track, &source.snapshot, timeline, at, &lane.policy),
            None => (overlays::NONE_CELL, 0),
        };
        if let Some(source) = lane.source.as_ref().filter(|_| value < FIRST_NON_SAMPLE) {
            named.entry((source.source, value)).or_insert(source);
        }
        // The bits an `Int32Array` over the Frame Map reads: -1 for none, -2 for empty.
        cells.push(value.cast_signed());
        kinds.push(kind);
    }
    let samples = named
        .iter()
        .filter(|(key, _)| !held.contains(key))
        .map(|(&(_, index), &source)| NewSample {
            source: source.clone(),
            index,
        })
        .collect();
    *held = named.into_keys().collect();
    Ok(RenderJob {
        seq,
        frame,
        samples,
        cells,
        kinds,
    })
}

/// A job submitted and not yet written, with the times its frame has in the source.
struct Pending {
    seq: u64,
    pts_us: i64,
    dur_us: i64,
}

impl Pending {
    fn of(spec: &ExportSpec, job: &RenderJob) -> Self {
        Self {
            seq: job.seq,
            pts_us: job.frame.id().timestamp.as_us(),
            dur_us: spec
                .index
                .duration_of(job.frame.id().sequence_id)
                .map_or_else(|| lone_frame_us(&spec.video), |period| period.as_us()),
        }
    }
}

/// How long the only frame of a video lasts: the period an image sequence declares, else [`LONE_FRAME_US`].
fn lone_frame_us(video: &VideoInput) -> i64 {
    match video {
        VideoInput::ImageSequence(sequence) => sequence
            .frame_rate
            .time_of(1)
            .map_or(LONE_FRAME_US, |period| period.as_us()),
        VideoInput::File(_) => LONE_FRAME_US,
    }
}

/// The encoder and the jobs it waits for.
struct Sink {
    encoder: Encoder,
    output: Output,
    size: (u32, u32),
    waiting: VecDeque<Pending>,
    done: u64,
    total: u64,
}

impl Sink {
    /// Takes the pixels of the oldest waiting job and encodes them.
    fn write_next(
        &mut self,
        renderer: &mut dyn Renderer,
        progress: &mut dyn FnMut(u64, u64),
    ) -> Result<(), ExportError> {
        let Some(pending) = self.waiting.pop_front() else {
            return Ok(());
        };
        let pixels = renderer.receive()?;
        if pixels.seq != pending.seq
            || pixels.output != self.output
            || (pixels.width, pixels.height) != self.size
        {
            return Err(fail(format!(
                "The renderer answered frame {} as {:?} {}x{}, not frame {} as {:?} {}x{}",
                pixels.seq,
                pixels.output,
                pixels.width,
                pixels.height,
                pending.seq,
                self.output,
                self.size.0,
                self.size.1
            ))
            .into());
        }
        self.encoder
            .write(pixels.picture()?, pending.pts_us, pending.dur_us)?;
        self.done += 1;
        progress(self.done, self.total);
        Ok(())
    }
}
