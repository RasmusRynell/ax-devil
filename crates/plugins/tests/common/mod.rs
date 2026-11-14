//! Shared by the plugin tests: a recording sink, a file source and manifest helpers.

#![allow(dead_code)] // each test binary uses a different part

use std::{
    fs,
    ops::ControlFlow,
    path::{Path, PathBuf},
    time::Duration,
};

use foundation::{CancelToken, Interrupt, Warning};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Header, Input, OnEqualKey, Record, SceneSink, Settings,
};
use plugins::{MANIFEST_FILE, PluginDecoder};
use scene::Scene;
use serde_json::{Value, json};

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

/// Records a live source would give: no path, positions from 1, and `gap` of waiting before each record after the
/// first.
pub struct Scripted {
    records: Vec<Vec<u8>>,
    gap: Duration,
    next: usize,
}

impl Scripted {
    pub fn new(records: &[&[u8]], gap: Duration) -> Self {
        Self {
            records: records.iter().map(|record| record.to_vec()).collect(),
            gap,
            next: 0,
        }
    }
}

impl Input for Scripted {
    fn path(&self) -> Option<&Path> {
        None
    }

    fn next(&mut self, _: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        let Some(bytes) = self.records.get(self.next) else {
            return Ok(None);
        };
        if self.next > 0 {
            std::thread::sleep(self.gap);
        }
        self.next += 1;
        Ok(Some(Record {
            bytes,
            position: self.next as u64,
            capture_time: None,
        }))
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

/// Decodes the records of `input` with `decoder` into a fresh sink.
pub fn decode_records(decoder: &PluginDecoder, input: &mut Scripted, cancel: &CancelToken) -> Run {
    let mut sink = TestSink::default();
    let options = decoder
        .spec()
        .options
        .validate(&Settings::new())
        .expect("no options");
    let result = decoder.decode(input, &options, &mut sink, cancel);
    Run { result, sink }
}

/// A manifest document with these fields; `extra` is inserted before the handlers as `,"key":value` text. Its one
/// decoder, `test-conv`, reads `.test` files.
pub fn manifest_text(program: &str, extra: &str) -> String {
    format!(
        r#"{{"manifest":2,"id":"test-plugin","name":"Test","version":"1","protocol":3,"program":{program}{extra},
            "handlers":[{{"kind":"decoder","id":"test-conv","name":"Test","input":"path","scene_version":"4.0",
            "extensions":[".test"]}}]}}"#
    )
}

/// A valid manifest for one plugin, `acme`, with one decoder.
pub fn base() -> Value {
    json!({
        "manifest": 2, "id": "acme", "name": "ACME", "version": "1", "protocol": 3,
        "program": ["python3", "convert.py"], "env": ["PYTHONPATH"], "code": ["convert.py"],
        "handlers": [{
            "kind": "decoder", "id": "acme", "name": "ACME", "input": "path", "scene_version": "4.0",
            "extensions": [".acme"],
            "options": [{ "key": "camera", "label": "Camera", "kind": "text", "default": "front" }]
        }]
    })
}

/// A plugin folder holding `manifest` and a `convert.py`; returns the manifest path.
pub fn install(folder: &Path, manifest: &Value) -> std::path::PathBuf {
    fs::create_dir_all(folder).expect("folder");
    fs::write(folder.join("convert.py"), "print('hi')\n").expect("script");
    let path = folder.join(MANIFEST_FILE);
    fs::write(&path, manifest.to_string()).expect("manifest");
    path
}
