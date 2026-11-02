//! Exports through a fake renderer: the file keeps the source's frame times, a failed or cancelled export leaves
//! nothing behind, and the renderer is sent each sample once while it holds it.

use std::{
    collections::{BTreeSet, VecDeque},
    fs,
    path::{Path, PathBuf},
    sync::Arc,
};

use export::{
    ExportError, ExportLane, ExportSpec, FrozenSource, IN_FLIGHT, LaneStyle, Output, Pixels,
    RenderJob, RenderSetup, Renderer, run,
};
use foundation::{CancelToken, ErrorCode, FrameRate, UserError};
use handlers::Handlers;
use media::{
    Decoder, EncodeFormat, Encoder, FrameIndex, ImageSequenceInput, Picture, Quality, VideoInput,
};
use overlay_sync::{MatchBasis, MatchPolicy, Sticky};
use playlist_resolvers::OverlayRef;
use scene_store::{Track, TrackRegistry};

const WIDTH: u32 = 64;
const HEIGHT: u32 = 48;

/// (start, duration) in microseconds of the six frames of the source: a variable frame rate.
const TIMES: [(i64, i64); 6] = [
    (0, 33_333),
    (33_333, 66_667),
    (100_000, 33_333),
    (133_333, 400_000),
    (533_333, 16_667),
    (550_000, 33_333),
];

/// Draws what the job's frame number says, so the output shows which frames were exported and in which order.
struct Fake {
    size: (u32, u32),
    set_up: Option<RenderSetup>,
    jobs: Vec<RenderJob>,
    answers: VecDeque<Pixels>,
    most_waiting: usize,
    fail_at: Option<u64>,
    cancel_at: Option<(u64, CancelToken)>,
}

impl Fake {
    fn new() -> Self {
        Self {
            size: (WIDTH, HEIGHT),
            set_up: None,
            jobs: Vec::new(),
            answers: VecDeque::new(),
            most_waiting: 0,
            fail_at: None,
            cancel_at: None,
        }
    }
}

/// The luma the fake draws for source frame `n`.
fn luma(n: u64) -> u8 {
    30 + 20 * n as u8
}

impl Renderer for Fake {
    fn setup(&mut self, setup: &RenderSetup) -> Result<(u32, u32), UserError> {
        self.set_up = Some(setup.clone());
        Ok(self.size)
    }

    fn submit(&mut self, job: RenderJob) -> Result<(), UserError> {
        let setup = self.set_up.as_ref().expect("setup comes first");
        let n = job.frame.id().sequence_id;
        if let Some((at, token)) = &self.cancel_at
            && *at == job.seq
        {
            token.cancel();
        }
        if self.fail_at == Some(job.seq) {
            return Err(UserError::new(ErrorCode::Internal, "the page crashed"));
        }
        let (w, h) = self.size;
        let pixels = match setup.output {
            Output::I420 => {
                let mut data = vec![luma(n); (w * h) as usize];
                data.extend(vec![128; (w * h / 2) as usize]);
                Pixels {
                    seq: job.seq,
                    output: Output::I420,
                    width: w,
                    height: h,
                    stride0: w,
                    stride1: w / 2,
                    data: data.into(),
                }
            }
            Output::Rgba => Pixels {
                seq: job.seq,
                output: Output::Rgba,
                width: w,
                height: h,
                stride0: w * 4,
                stride1: 0,
                data: (0..w * h)
                    .flat_map(|i| [(i % w) as u8, (i / w) as u8, n as u8, 255])
                    .collect::<Vec<u8>>()
                    .into(),
            },
        };
        self.answers.push_back(pixels);
        self.most_waiting = self.most_waiting.max(self.answers.len());
        self.jobs.push(job);
        Ok(())
    }

    fn receive(&mut self) -> Result<Pixels, UserError> {
        Ok(self.answers.pop_front().expect("a job is waiting"))
    }
}

/// A six-frame MP4 at the times in [`TIMES`] in `dir`, and its index.
fn source(dir: &Path) -> (PathBuf, Arc<FrameIndex>) {
    let path = dir.join("source.mp4");
    let quality = Quality::Balanced;
    let mut encoder = Encoder::create(&path, EncodeFormat::Mp4 { quality }, WIDTH, HEIGHT).unwrap();
    let (y, u, v) = (
        vec![100; (WIDTH * HEIGHT) as usize],
        vec![128; (WIDTH * HEIGHT / 4) as usize],
        vec![128; (WIDTH * HEIGHT / 4) as usize],
    );
    for (start, duration) in TIMES {
        let picture = Picture::I420 {
            y: &y,
            u: &u,
            v: &v,
            y_stride: WIDTH as usize,
            c_stride: WIDTH as usize / 2,
        };
        encoder.write(picture, start, duration).unwrap();
    }
    encoder.finish().unwrap();
    let index = index_of(&VideoInput::File(path.clone()));
    (path, index)
}

fn index_of(input: &VideoInput) -> Arc<FrameIndex> {
    let index = FrameIndex::load_or_build(input, None, &CancelToken::new(), &mut |_| {});
    Arc::new(index.expect("an index"))
}

fn plain_lane() -> ExportLane {
    ExportLane {
        source: None,
        policy: MatchPolicy::default(),
        catalog: "{}".into(),
        style: LaneStyle {
            label: "lane".into(),
            enabled_classes: Vec::new(),
            class_options: 0,
            hidden: 0,
            theme: None,
            retained_opacity: 0.5,
            filter: None,
        },
    }
}

fn spec(
    video: &Path,
    index: &Arc<FrameIndex>,
    frames: std::ops::RangeInclusive<u64>,
    destination: PathBuf,
) -> ExportSpec {
    ExportSpec {
        video: VideoInput::File(video.to_path_buf()),
        index: index.clone(),
        frames,
        lanes: vec![plain_lane()],
        format: EncodeFormat::Mp4 {
            quality: Quality::Balanced,
        },
        destination,
        inputs: Vec::new(),
    }
}

fn names(dir: &Path) -> BTreeSet<String> {
    fs::read_dir(dir)
        .unwrap()
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .collect()
}

fn export(spec: &ExportSpec, fake: &mut Fake) -> Result<(), ExportError> {
    run(spec, fake, &CancelToken::new(), &mut |_, _| {})
}

#[test]
fn a_range_keeps_the_times_of_its_frames_and_replaces_the_destination() {
    let dir = tempfile::tempdir().unwrap();
    let (video, index) = source(dir.path());
    let out = dir.path().join("out.mp4");
    fs::write(&out, "old").unwrap();
    let mut fake = Fake::new();
    let mut reported = Vec::new();

    run(
        &spec(&video, &index, 1..=5, out.clone()),
        &mut fake,
        &CancelToken::new(),
        &mut |done, total| reported.push((done, total)),
    )
    .expect("the export");

    assert_eq!(reported, [(0, 5), (1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]);
    assert!(fake.most_waiting <= IN_FLIGHT);
    assert_eq!(
        names(dir.path()),
        BTreeSet::from(["out.mp4".into(), "source.mp4".into()])
    );
    let exported = index_of(&VideoInput::File(out.clone()));
    let times: Vec<i64> = exported
        .timeline()
        .as_slice()
        .iter()
        .map(|t| t.as_us())
        .collect();
    let expected: Vec<i64> = (1..=5).map(|n| TIMES[n].0 - TIMES[1].0).collect();
    assert_eq!(
        times, expected,
        "the source's times, from the first exported frame"
    );
    // The last frame keeps the duration the source gave it, not the gap before it (16,667 us).
    let last = exported.duration_of(4).map(|d| d.as_us());
    assert!(
        last.is_some_and(|us| (us - TIMES[5].1).abs() <= 1),
        "{last:?}"
    );
    let mut decoder =
        Decoder::open(&VideoInput::File(out), exported, 1, CancelToken::new()).unwrap();
    for n in 1..=5u64 {
        let frame = decoder.next_frame().unwrap().expect("a frame");
        let drawn = i32::from(luma(n));
        assert!(
            frame
                .plane(0)
                .iter()
                .all(|&y| (i32::from(y) - drawn).abs() <= 6),
            "frame {n}"
        );
    }
    assert!(decoder.next_frame().unwrap().is_none());

    let past_the_end = spec(&video, &index, 4..=6, dir.path().join("late.mp4"));
    assert!(matches!(
        export(&past_the_end, &mut fake),
        Err(ExportError::Failed(_))
    ));
    assert!(!dir.path().join("late.mp4").exists());
}

#[test]
fn a_cancelled_or_failed_export_leaves_the_destination_and_the_folder_as_they_were() {
    let dir = tempfile::tempdir().unwrap();
    let (video, index) = source(dir.path());
    let out = dir.path().join("out.mp4");
    fs::write(&out, "old").unwrap();
    let before = names(dir.path());

    let token = CancelToken::new();
    let mut cancelling = Fake::new();
    cancelling.cancel_at = Some((2, token.clone()));
    // The renderer fails the moment it is cancelled, as a page does when its waits are cut short: still a cancel.
    cancelling.fail_at = Some(2);
    let result = run(
        &spec(&video, &index, 0..=5, out.clone()),
        &mut cancelling,
        &token,
        &mut |_, _| {},
    );
    assert_eq!(result, Err(ExportError::Cancelled));
    assert_eq!(fs::read_to_string(&out).unwrap(), "old");
    assert_eq!(names(dir.path()), before);

    let mut failing = Fake::new();
    failing.fail_at = Some(3);
    let result = export(&spec(&video, &index, 0..=5, out.clone()), &mut failing);
    assert!(
        matches!(result, Err(ExportError::Failed(error)) if error.reason == "the page crashed")
    );
    assert_eq!(fs::read_to_string(&out).unwrap(), "old");
    assert_eq!(names(dir.path()), before);
}

#[test]
fn an_output_that_would_overwrite_an_input_or_cannot_be_written_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let (video, index) = source(dir.path());
    let shot = dir.path().join("shot.png");
    fs::write(&shot, "x").unwrap();
    let before = names(dir.path());
    let video_bytes = fs::read(&video).unwrap();

    let mp4 = EncodeFormat::Mp4 {
        quality: Quality::Balanced,
    };
    let cases = [
        (mp4, video.clone(), vec![]),
        (mp4, dir.path().join(".").join("source.mp4"), vec![]),
        (EncodeFormat::Png, shot.clone(), vec![shot.clone()]),
        (mp4, PathBuf::from("out.mp4"), vec![]),
        (mp4, dir.path().join("out.mov"), vec![]),
        (mp4, dir.path().join("missing").join("out.mp4"), vec![]),
    ];
    for (format, destination, inputs) in cases {
        let mut fake = Fake::new();
        let frames = if format == EncodeFormat::Png {
            0..=0
        } else {
            0..=5
        };
        let mut job = spec(&video, &index, frames, destination.clone());
        (job.format, job.inputs) = (format, inputs);
        let result = export(&job, &mut fake);
        assert!(
            matches!(&result, Err(ExportError::Failed(error)) if error.code == ErrorCode::InvalidPath),
            "{destination:?}: {result:?}"
        );
        assert!(
            fake.set_up.is_none(),
            "{destination:?} reached the renderer"
        );
    }
    assert_eq!(fs::read(&video).unwrap(), video_bytes);
    assert_eq!(fs::read_to_string(&shot).unwrap(), "x");
    assert_eq!(names(dir.path()), before);
}

/// The track of an ONVIF file with two samples, at 0 and 0.5 s on the video's clock, and the file.
fn two_sample_track(dir: &Path) -> (PathBuf, Arc<Track>) {
    let mot = dir.join("det.xml");
    let frame = |time: &str| {
        format!(
            r#"<tt:Frame xmlns:tt="http://www.onvif.org/ver10/schema" UtcTime="1970-01-01T00:00:{time}Z"><tt:Object ObjectId="obj-1"><tt:Appearance><tt:Class><tt:Type Likelihood="0.9">Car</tt:Type></tt:Class><tt:Shape><tt:BoundingBox left="0" top="0.4" right="0.4" bottom="0" /></tt:Shape></tt:Appearance></tt:Object></tt:Frame>"#
        )
    };
    fs::write(&mot, format!("{}\n{}\n", frame("00"), frame("00.5"))).unwrap();
    let overlay = OverlayRef {
        label: "det".into(),
        path: mot.clone(),
        handler: "ONVIF_XML".into(),
        options: serde_json::Map::new(),
    };
    let track = TrackRegistry::new(Handlers::built_in(), None)
        .open(&overlay, &CancelToken::new())
        .expect("the track");
    (mot, track)
}

#[test]
fn the_renderer_is_sent_each_sample_once_while_it_holds_it() {
    let dir = tempfile::tempdir().unwrap();
    let (video, index) = source(dir.path());
    let (mot, track) = two_sample_track(dir.path());
    let on_source = |sticky: bool| ExportLane {
        source: Some(FrozenSource {
            track: track.clone(),
            data_revision: 1,
            snapshot: track.snapshot(),
            source: 7,
        }),
        policy: MatchPolicy {
            basis: Some(MatchBasis::Time),
            sticky: Sticky {
                enabled: sticky,
                timeout: None,
            },
            ..MatchPolicy::default()
        },
        ..plain_lane()
    };
    let mut job = spec(&video, &index, 0..=5, dir.path().join("out.mp4"));
    job.lanes = vec![on_source(true), on_source(false), plain_lane()];
    job.inputs = vec![mot];
    let mut fake = Fake::new();

    export(&job, &mut fake).expect("the export");

    // Sample 0 is shown on frames 0 to 3 by the lane that keeps it, and sent once; sample 1 on frames 4 and 5.
    let sent: Vec<Vec<u32>> = fake
        .jobs
        .iter()
        .map(|job| job.samples.iter().map(|sample| sample.index).collect())
        .collect();
    assert_eq!(sent, [vec![0], vec![], vec![], vec![], vec![1], vec![]]);
    assert!(
        fake.jobs
            .iter()
            .flat_map(|job| &job.samples)
            .all(|sample| sample.source.source == 7)
    );
    let drawn: Vec<Vec<i32>> = fake.jobs.iter().map(|job| job.cells.clone()).collect();
    assert_eq!(
        drawn,
        [
            [0, 0, -1],
            [0, 0, -1],
            [0, -1, -1],
            [0, -1, -1],
            [1, 1, -1],
            [1, 1, -1]
        ]
    );
    assert_eq!(
        fake.set_up
            .expect("a setup")
            .lanes
            .iter()
            .map(|lane| lane.source)
            .collect::<Vec<_>>(),
        [Some(7), Some(7), None]
    );
}

#[test]
fn a_still_is_one_frame_written_as_a_png() {
    let dir = tempfile::tempdir().unwrap();
    let (video, index) = source(dir.path());
    let mut job = spec(&video, &index, 2..=3, dir.path().join("0.png"));
    job.format = EncodeFormat::Png;
    let mut fake = Fake::new();
    assert!(
        matches!(export(&job, &mut fake), Err(ExportError::Failed(_))),
        "a still is one frame"
    );
    assert!(!dir.path().join("0.png").exists());

    job.frames = 3..=3;
    export(&job, &mut fake).expect("the export");

    let input = VideoInput::ImageSequence(ImageSequenceInput {
        pattern: dir.path().join("%d.png"),
        start_number: 0,
        frame_rate: FrameRate::parse("1").unwrap(),
        frame_count: 1,
    });
    let mut decoder = Decoder::open(&input, index_of(&input), 1, CancelToken::new()).unwrap();
    let picture = decoder.next_frame().unwrap().expect("the picture");
    let (width, height) = (WIDTH as usize, HEIGHT as usize);
    for (at, pixel) in picture.plane(0).chunks(4).enumerate().take(width * height) {
        assert_eq!(
            pixel,
            [(at % width) as u8, (at / width) as u8, 3, 255],
            "pixel {at}"
        );
    }
}
