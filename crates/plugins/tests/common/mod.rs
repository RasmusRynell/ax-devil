//! Shared by the plugin tests: a recording sink, a file source and manifest helpers.

#![allow(dead_code)] // each test binary uses a different part

use std::{
    ops::ControlFlow,
    path::{Path, PathBuf},
};

use foundation::{CancelToken, Interrupt, Warning};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Header, Input, OnEqualKey, Record, SceneSink, Settings,
};
use plugins::PluginDecoder;
use scene::Scene;

/// Records what a decoder gives its sink; optionally cancels after some samples.
#[derive(Default)]
pub struct TestSink {
    pub header: Option<Header>,
    pub samples: Vec<(Scene, OnEqualKey)>,
    pub skips: Vec<(u64, String)>,
    pub warnings: Vec<Warning>,
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

    fn skip(&mut self, position: u64, reason: String) {
        self.skips.push((position, reason));
    }

    fn warn(&mut self, warning: Warning) {
        self.warnings.push(warning);
    }
}

/// A file the decoder is pointed at; plugin decoders never ask for its bytes.
pub struct FileSource(pub PathBuf);

impl Input for FileSource {
    fn path(&self) -> Option<&Path> {
        Some(&self.0)
    }

    fn next(&mut self, _: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        panic!("a plugin decoder reads the file itself");
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
    decoder: &PluginDecoder,
    file: &Path,
    options: &Settings,
    cancel: &CancelToken,
    mut sink: TestSink,
) -> Run {
    let options = decoder
        .spec()
        .options
        .validate(options)
        .expect("the options fit the decoder's form");
    let result = decoder.decode(
        &mut FileSource(file.to_path_buf()),
        &options,
        &mut sink,
        cancel,
    );
    Run { result, sink }
}

/// A manifest document with these fields; `extra` is inserted before the handlers as `,"key":value` text. Its one
/// decoder, `test-conv`, reads `.test` files.
pub fn manifest_text(command: &str, extra: &str) -> String {
    format!(
        r#"{{"manifest":2,"id":"test-plugin","name":"Test","version":"1","protocol":3,"command":{command}{extra},
            "handlers":[{{"kind":"decoder","id":"test-conv","name":"Test","input":"path","scene_version":"4.0",
            "extensions":[".test"]}}]}}"#
    )
}
