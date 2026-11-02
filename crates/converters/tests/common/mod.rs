//! Shared by the converter tests: a recording sink, a file source and manifest helpers.

#![allow(dead_code)] // each test binary uses a different part

use std::{
    ops::ControlFlow,
    path::{Path, PathBuf},
};

use converters::ConverterDecoder;
use foundation::{CancelToken, FrameSize, Interrupt};
use scene::Scene;
use scene_decoders::{
    DecodeContext, DecodeError, DecodeOptions, DecodeSummary, FileSceneDecoder, Header,
    LoadWarning, OnEqualKey, SceneSink, SourceFile,
};

/// Records what a decoder gives its sink; optionally cancels after some samples.
#[derive(Default)]
pub struct TestSink {
    pub header: Option<Header>,
    pub samples: Vec<(Scene, OnEqualKey)>,
    pub warnings: Vec<LoadWarning>,
    pub cancel_after: Option<(usize, CancelToken)>,
}

impl SceneSink for TestSink {
    fn header(&mut self, header: Header) {
        assert!(self.header.is_none(), "header given twice");
        assert!(self.samples.is_empty(), "header after a sample");
        self.header = Some(header);
    }

    fn sample(&mut self, scene: Scene, on_equal_key: OnEqualKey) -> ControlFlow<()> {
        self.samples.push((scene, on_equal_key));
        if let Some((count, token)) = &self.cancel_after
            && self.samples.len() == *count
        {
            token.cancel();
        }
        ControlFlow::Continue(())
    }

    fn warn(&mut self, warning: LoadWarning) {
        self.warnings.push(warning);
    }
}

/// A file the decoder is pointed at; converters never ask for its bytes.
pub struct FileSource(pub PathBuf);

impl SourceFile for FileSource {
    fn path(&self) -> &Path {
        &self.0
    }

    fn bytes(&self, _: &CancelToken) -> Result<&[u8], Interrupt<DecodeError>> {
        panic!("a converter reads the file itself");
    }
}

/// How a run ended: what the decoder returned and what the sink saw.
pub struct Run {
    pub result: Result<DecodeSummary, Interrupt<DecodeError>>,
    pub sink: TestSink,
}

impl Run {
    /// The reason the run failed.
    pub fn failure(&self) -> &str {
        match &self.result {
            Err(Interrupt::Failed(error)) => &error.0,
            Err(Interrupt::Cancelled) => panic!("the run was cancelled"),
            Ok(_) => panic!("the run completed"),
        }
    }

    /// The summary of a run that completed.
    pub fn summary(&self) -> &DecodeSummary {
        match &self.result {
            Ok(summary) => summary,
            Err(Interrupt::Failed(error)) => panic!("the run failed: {error}"),
            Err(Interrupt::Cancelled) => panic!("the run was cancelled"),
        }
    }
}

/// Decodes `file` with `decoder` into a fresh sink.
pub fn decode(
    decoder: &ConverterDecoder,
    file: &Path,
    options: &DecodeOptions,
    cancel: &CancelToken,
    mut sink: TestSink,
) -> Run {
    let context = DecodeContext {
        options,
        frame_size: FrameSize {
            width: 1920,
            height: 1080,
        },
    };
    let result = decoder.decode_file(&FileSource(file.to_path_buf()), &context, &mut sink, cancel);
    Run { result, sink }
}

/// A manifest document with these fields; `extra` is inserted before the closing brace as `, "key": value` text.
pub fn manifest_text(command: &str, extra: &str) -> String {
    format!(
        r#"{{"manifest":1,"kind":"file","id":"test-conv","name":"Test","version":"1","protocol":1,
            "scene_version":"4.0","extensions":[".test"],"command":{command}{extra}}}"#
    )
}
