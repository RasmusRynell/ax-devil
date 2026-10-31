//! A fake decoder and helpers for driving workers step by step, without threads or timing.

use crate::source::Source;
use crate::worker::{Step, Worker};
use crate::{CachePool, DecoderFactory, Delivery, FrameSet, Session, SourceSpec, SpanDecoder};
use bytes::Bytes;
use foundation::{
    ColorDesc, FrameFailure, FrameId, FrameLayout, Keyframes, Matrix, MediaTime, PixelFormat,
    Plane, Timeline, UserError, VideoFrame,
};
use std::collections::BTreeMap;
use std::future::Future;
use std::pin::pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Wake, Waker};
use std::thread::{self, Thread};
use std::time::{Duration, Instant};

pub(crate) const FRAME_BYTES: usize = 100;

type Hook = Box<dyn FnMut(u64) + Send>;

/// What every fake decoder of a fixture did, and what it should do next.
#[derive(Default)]
pub(crate) struct Log {
    pub(crate) decoded: Mutex<Vec<u64>>,
    pub(crate) seeks: Mutex<Vec<u64>>,
    pub(crate) failing: Mutex<BTreeMap<u64, UserError>>,
    pub(crate) panic_at: Mutex<Option<u64>>,
    /// Runs inside `next_frame`, with no playback lock held, as the frame is decoded.
    pub(crate) hook: Mutex<Option<Hook>>,
}

impl Log {
    pub(crate) fn decoded(&self) -> Vec<u64> {
        crate::lock(&self.decoded).clone()
    }

    pub(crate) fn seeks(&self) -> Vec<u64> {
        crate::lock(&self.seeks).clone()
    }

    pub(crate) fn on_frame(&self, hook: impl FnMut(u64) + Send + 'static) {
        *crate::lock(&self.hook) = Some(Box::new(hook));
    }
}

pub(crate) fn video_frame(id: u64, bytes: usize) -> VideoFrame {
    let unused = Plane { stride: 0, len: 0 };
    let layout = FrameLayout {
        width: 1,
        height: 1,
        format: PixelFormat::Rgba,
        color: ColorDesc {
            matrix: Matrix::Bt709,
            full_range: true,
        },
        // One plane of one-byte rows, so any size is whole rows.
        planes: [
            Plane {
                stride: 1,
                len: u32::try_from(bytes).unwrap(),
            },
            unused,
            unused,
        ],
    };
    let frame_id = FrameId {
        sequence_id: id,
        timestamp: MediaTime::from_us(i64::try_from(id).unwrap_or(0) * 40_000),
    };
    VideoFrame::new(frame_id, false, layout, Bytes::from(vec![0; bytes])).unwrap()
}

pub(crate) struct FakeDecoder {
    log: Arc<Log>,
    frames: u64,
    position: Option<u64>,
}

impl SpanDecoder for FakeDecoder {
    fn position(&self) -> Option<u64> {
        self.position
    }

    fn seek_to_keyframe(&mut self, keyframe: u64) -> Result<(), UserError> {
        crate::lock(&self.log.seeks).push(keyframe);
        self.position = Some(keyframe);
        Ok(())
    }

    fn next_frame(&mut self) -> Result<Option<VideoFrame>, FrameFailure> {
        let Some(id) = self.position else {
            return Ok(None);
        };
        if id >= self.frames {
            self.position = None;
            return Ok(None);
        }
        crate::lock(&self.log.decoded).push(id);
        if let Some(hook) = crate::lock(&self.log.hook).as_mut() {
            hook(id);
        }
        if crate::lock(&self.log.panic_at)
            .take_if(|at| *at == id)
            .is_some()
        {
            panic!("the fake decoder was told to panic at frame {id}");
        }
        self.position = Some(id + 1);
        if let Some(error) = crate::lock(&self.log.failing).get(&id) {
            return Err(FrameFailure {
                sequence_id: id,
                error: error.clone(),
            });
        }
        Ok(Some(video_frame(id, FRAME_BYTES)))
    }
}

pub(crate) fn spec(frames: u64, gop: u64) -> SourceSpec {
    let times = (0..frames)
        .map(|id| MediaTime::from_us(i64::try_from(id).unwrap_or(0) * 40_000))
        .collect();
    SourceSpec {
        timeline: Timeline::new(times).unwrap(),
        keyframes: Keyframes::new((0..frames).step_by(gop as usize).collect(), frames).unwrap(),
        frame_bytes: FRAME_BYTES,
    }
}

/// One source with fake decoders and no threads: the test decides when each worker takes a turn.
pub(crate) struct Fixture {
    pub(crate) pool: Arc<CachePool>,
    pub(crate) log: Arc<Log>,
    pub(crate) source: Arc<Source>,
    workers: Vec<Worker>,
}

pub(crate) fn factory(log: &Arc<Log>, frames: u64) -> DecoderFactory {
    let log = Arc::clone(log);
    Arc::new(move |_, _| {
        Ok(Box::new(FakeDecoder {
            log: Arc::clone(&log),
            frames,
            position: Some(0),
        }))
    })
}

impl Fixture {
    /// A video of `frames` frames with a keyframe every `gop`, and a cache holding `share_frames` frames.
    pub(crate) fn new(frames: u64, gop: u64, share_frames: usize) -> Self {
        Self::with_allowance(frames, gop, share_frames * FRAME_BYTES)
    }

    pub(crate) fn with_allowance(frames: u64, gop: u64, allowance: usize) -> Self {
        let log = Arc::new(Log::default());
        let factory = factory(&log, frames);
        Self::build(frames, gop, allowance, log, factory)
    }

    /// A fixture whose decoders come from `factory` instead of the fake one.
    pub(crate) fn with_factory(
        frames: u64,
        gop: u64,
        share_frames: usize,
        factory: DecoderFactory,
    ) -> Self {
        let log = Arc::new(Log::default());
        Self::build(frames, gop, share_frames * FRAME_BYTES, log, factory)
    }

    fn build(
        frames: u64,
        gop: u64,
        allowance: usize,
        log: Arc<Log>,
        factory: DecoderFactory,
    ) -> Self {
        let pool = CachePool::new(allowance);
        let source = Source::new(
            spec(frames, gop),
            factory,
            pool.add_source(),
            "video.mp4".to_string(),
        );
        Self {
            pool,
            log,
            source,
            workers: (0..crate::DECODE_WORKERS).map(Worker::new).collect(),
        }
    }

    pub(crate) fn session(&self) -> Session {
        Session::join(&self.source).unwrap()
    }

    pub(crate) fn step(&mut self, worker: usize) -> Step {
        self.source.step(&mut self.workers[worker])
    }

    /// Lets the workers take turns until none has anything left to do.
    pub(crate) fn drain(&mut self) {
        for _ in 0..10_000 {
            let mut ran = false;
            for worker in &mut self.workers {
                ran |= self.source.step(worker) == Step::Ran;
            }
            if !ran {
                return;
            }
        }
        panic!("the decode workers never went idle");
    }

    pub(crate) fn cached(&self) -> FrameSet {
        self.source.cache.ranges()
    }
}

/// Polls once: `None` while nothing is ready.
pub(crate) fn poll_now<F: Future>(future: F) -> Option<F::Output> {
    let mut future = pin!(future);
    match future
        .as_mut()
        .poll(&mut Context::from_waker(Waker::noop()))
    {
        Poll::Ready(value) => Some(value),
        Poll::Pending => None,
    }
}

struct Unpark(Thread);

impl Wake for Unpark {
    fn wake(self: Arc<Self>) {
        self.0.unpark();
    }
}

/// Waits for `future` on this thread, at most `failsafe`: `None` when it did not finish. The bound exists so a
/// regression fails a test instead of hanging it; no test asserts how long anything takes.
pub(crate) fn block_on<F: Future>(future: F, failsafe: Duration) -> Option<F::Output> {
    let waker = Waker::from(Arc::new(Unpark(thread::current())));
    let mut context = Context::from_waker(&waker);
    let mut future = pin!(future);
    let deadline = Instant::now() + failsafe;
    loop {
        if let Poll::Ready(value) = future.as_mut().poll(&mut context) {
            return Some(value);
        }
        let left = deadline.checked_duration_since(Instant::now())?;
        thread::park_timeout(left);
    }
}

/// Everything ready for the session now, as (frame, is a failure), in delivery order.
pub(crate) fn deliveries(session: &mut Session) -> Vec<(u64, bool)> {
    let mut out = Vec::new();
    while let Some(Some(delivery)) = poll_now(session.next_delivery()) {
        out.push(match delivery {
            Delivery::Frame(frame) => (frame.id().sequence_id, false),
            Delivery::Failure(failure) => (failure.sequence_id, true),
            Delivery::Failed(error) => panic!("the source failed: {error}"),
        });
    }
    out
}

pub(crate) fn want(
    playhead: u64,
    ahead: u32,
    behind: u32,
    direction: crate::Direction,
) -> crate::Want {
    crate::Want {
        playhead,
        ahead,
        behind,
        direction,
    }
}
