//! The host frames a file into lines for a decoder that reads records, and counts the ones it skips.

use super::*;

/// A line decoder: a line starting with `bad` is skipped, any other gives a frame Scene numbered by the line.
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
                let scene = entities(SceneTime::Frame(record.position), 1);
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

#[test]
fn the_host_frames_lines_and_counts_the_ones_a_decoder_skips() {
    let dir = tempfile::tempdir().expect("temp dir");
    let source = dir.path().join("overlay.txt");
    fs::write(&source, b"  one \n\nbad\n two\r\n").expect("source");
    let decoder = Arc::new(Lines(Spec {
        framing: Framing::Records,
        ..spec(1)
    }));
    let registry = TrackRegistry::new(move |_| Some(decoder.clone() as Arc<dyn Decoder>), None);
    let overlay = OverlayRef {
        label: "lane".into(),
        slot: "lane".into(),
        path: source,
        handler: "TEST".into(),
        options: Settings::from_iter([("unknown".to_owned(), "x".into())]),
    };

    let track = registry.open(&overlay, &CancelToken::new()).expect("opens");

    assert_eq!(
        track.snapshot().keys,
        frame_keys(&[1, 4]),
        "blank lines are not records"
    );
    let warnings = track.warnings();
    let [undeclared, skipped] = &warnings[..] else {
        panic!("{warnings:?}")
    };
    assert_eq!(
        undeclared.reasons,
        ["'unknown'"],
        "an option the decoder does not declare"
    );
    assert_eq!(skipped.problem, "Skipped 1 of 3 lines");
    assert_eq!(skipped.reasons, ["line 3: not a record"]);
}
