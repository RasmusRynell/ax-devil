//! The host frames a file into lines for a decoder that reads records, and counts the ones it skips.

use super::*;

/// A line decoder: a line starting with `bad` is skipped, any other gives a Scene timed by the line.
struct Lines(Spec);

impl Decoder for Lines {
    fn spec(&self) -> &Spec {
        &self.0
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        while let Some(record) = input.next(cancel)? {
            if record.bytes.starts_with(b"bad") {
                sink.skip(record.position, "not a record".into());
            } else {
                let time = MediaTime(record.position as i64 * 1_000);
                let scene = entities(SceneTime::Timestamp(time), 1);
                let _ = sink.sample(scene, OnEqualKey::Replace);
            }
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: FilterPolicy {
                name: "lines",
                options: Vec::new(),
            },
            metadata: Vec::new(),
            coverage: Coverage::Sparse,
        })
    }
}

/// The track of a file with `text` read by `decoder`.
fn open(decoder: impl Decoder + 'static, text: &[u8]) -> Arc<Track> {
    let dir = tempfile::tempdir().expect("temp dir");
    let source = dir.path().join("overlay.txt");
    fs::write(&source, text).expect("source");
    let decoder: Arc<dyn Decoder> = Arc::new(decoder);
    let registry = TrackRegistry::new(move |_| Some(decoder.clone()), None);
    let overlay = OverlayRef {
        label: "lane".into(),
        slot: "lane".into(),
        path: source,
        handler: "TEST".into(),
        options: Settings::new(),
    };
    registry.open(&overlay, &CancelToken::new()).expect("opens")
}

#[test]
fn the_host_frames_lines_and_a_skipped_one_keeps_its_frame() {
    let decoder = Lines(Spec {
        framing: Framing::Records,
        ..spec(1)
    });
    let track = open(decoder, b"  one \n\nbad\n two\r\n");

    assert_eq!(
        track.snapshot().keys.frames,
        Some(in_order(&[0, 2])),
        "blank lines are not records and a skipped one holds its frame"
    );
    let warnings = track.warnings();
    let [skipped] = &warnings[..] else {
        panic!("{warnings:?}")
    };
    assert_eq!(skipped.problem, "Skipped 1 of 3 lines");
    assert_eq!(skipped.reasons, ["line 3: not a record"]);
}

/// A decoder that reads the file itself, as a plugin does, and gives up on lines of its own.
struct ReadsPath(Spec);

impl Decoder for ReadsPath {
    fn spec(&self) -> &Spec {
        &self.0
    }

    fn decode(
        &self,
        _input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        _cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        let at = |ms: i64| SceneTime::Timestamp(MediaTime(ms * 1_000));
        // One record of three Scenes (one per object), a record it gave up on, and a record.
        let _ = sink.sample(entities(at(0), 1), OnEqualKey::Merge);
        let _ = sink.sample(entities(at(0), 1), OnEqualKey::Merge);
        let _ = sink.sample(entities(at(0), 1), OnEqualKey::Merge);
        sink.skip(3, "not a sample".into());
        let _ = sink.sample(entities(at(2), 1), OnEqualKey::Merge);
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: FilterPolicy {
                name: "reads-path",
                options: Vec::new(),
            },
            metadata: Vec::new(),
            coverage: Coverage::Sparse,
        })
    }
}

#[test]
fn a_decoder_that_reads_the_file_itself_places_its_records_by_frame() {
    let track = open(ReadsPath(spec(1)), b"unread\n");

    assert_eq!(
        track.snapshot().keys.frames,
        Some(in_order(&[0, 2])),
        "a record's Scenes share its frame and a skipped record holds its own"
    );
    let warnings = track.warnings();
    let [skipped] = &warnings[..] else {
        panic!("{warnings:?}")
    };
    assert_eq!(skipped.problem, "Skipped 1 of 3 lines");
    assert_eq!(skipped.reasons, ["line 3: not a sample"]);
}
