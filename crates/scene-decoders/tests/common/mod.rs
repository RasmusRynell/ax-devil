//! A record source and a sink that keep everything in memory, for running a decoder the way the store does.

#![allow(dead_code)] // each test binary uses a different part

use std::{ops::ControlFlow, path::Path, sync::Arc};

use foundation::{CancelToken, Interrupt, Warning};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Header, Input, OnEqualKey, Record, SceneSink, Settings,
};
use scene::Scene;

/// The built-in decoder with this id.
pub fn decoder(id: &str) -> Arc<dyn Decoder> {
    scene_decoders::decoders()
        .into_iter()
        .find(|decoder| decoder.spec().id == id)
        .unwrap_or_else(|| panic!("{id} is built in"))
}

/// Records that are already in memory, each with its position.
pub struct Memory<'a>(pub Vec<(u64, &'a [u8])>);

impl Input for Memory<'_> {
    fn path(&self) -> Option<&Path> {
        None
    }

    fn next(&mut self, cancel: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        cancel.check()?;
        if self.0.is_empty() {
            return Ok(None);
        }
        let (position, bytes) = self.0.remove(0);
        Ok(Some(Record {
            bytes,
            position,
            capture_time: None,
        }))
    }
}

/// The non-blank lines of `bytes`, trimmed and numbered from 1 by their place, as the store frames them.
pub fn lines(bytes: &[u8]) -> Memory<'_> {
    let numbered = bytes.split(|&byte| byte == b'\n').zip(1..);
    Memory(
        numbered
            .map(|(line, number)| (number, line.trim_ascii()))
            .filter(|(_, line)| !line.is_empty())
            .collect(),
    )
}

/// Every Scene, skip and warning a decoder gave, in order.
#[derive(Default)]
pub struct Collected {
    pub scenes: Vec<Scene>,
    pub skipped: Vec<(u64, String)>,
    pub warnings: Vec<Warning>,
}

impl SceneSink for Collected {
    fn header(&mut self, _header: Header) {}

    fn sample(&mut self, scene: Scene, _on_equal_key: OnEqualKey) -> ControlFlow<()> {
        self.scenes.push(scene);
        ControlFlow::Continue(())
    }

    fn skip(&mut self, position: u64, reason: String) {
        self.skipped.push((position, reason));
    }

    fn warn(&mut self, warning: Warning) {
        self.warnings.push(warning);
    }
}

/// Runs `decoder` over `input` with `options` checked against its form; what it gave is returned even when the
/// decode failed.
pub fn run_input(
    decoder: &dyn Decoder,
    input: &mut dyn Input,
    options: &Settings,
    cancel: &CancelToken,
) -> (Collected, Result<DecodeSummary, Interrupt<DecodeError>>) {
    let options = decoder
        .spec()
        .options
        .validate(options)
        .expect("the options fit the decoder's form");
    let mut sink = Collected::default();
    let result = decoder.decode(input, &options, &mut sink, cancel);
    (sink, result)
}

/// Runs a whole-file `decoder` over `bytes`.
pub fn run(
    decoder: &dyn Decoder,
    bytes: &[u8],
    options: &Settings,
    cancel: &CancelToken,
) -> (Vec<Scene>, Result<DecodeSummary, Interrupt<DecodeError>>) {
    let (sink, result) = run_input(decoder, &mut Memory(vec![(0, bytes)]), options, cancel);
    (sink.scenes, result)
}
