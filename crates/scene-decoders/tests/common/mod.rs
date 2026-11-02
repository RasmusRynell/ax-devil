//! A source and a sink that keep everything in memory, for running a decoder the way the store does.

use std::{ops::ControlFlow, path::Path};

use foundation::{CancelToken, Interrupt};
use scene::Scene;
use scene_decoders::{
    DecodeContext, DecodeError, DecodeSummary, FileSceneDecoder, Header, LoadWarning, OnEqualKey,
    SceneSink, SourceFile,
};

/// A file whose bytes are already in memory.
pub struct BytesSource<'a>(pub &'a [u8]);

impl SourceFile for BytesSource<'_> {
    fn path(&self) -> &Path {
        Path::new("memory")
    }

    fn bytes(&self, cancel: &CancelToken) -> Result<&[u8], Interrupt<DecodeError>> {
        cancel.check()?;
        Ok(self.0)
    }
}

/// Every Scene and warning a decoder gave, in order.
#[derive(Default)]
pub struct Collected {
    pub scenes: Vec<Scene>,
    pub warnings: Vec<LoadWarning>,
}

impl SceneSink for Collected {
    fn header(&mut self, _header: Header) {}

    fn sample(&mut self, scene: Scene, _on_equal_key: OnEqualKey) -> ControlFlow<()> {
        self.scenes.push(scene);
        ControlFlow::Continue(())
    }

    fn warn(&mut self, warning: LoadWarning) {
        self.warnings.push(warning);
    }
}

/// Runs `decoder` over `bytes`; the Scenes it gave are returned even when the decode failed.
pub fn run(
    decoder: &dyn FileSceneDecoder,
    bytes: &[u8],
    context: &DecodeContext<'_>,
    cancel: &CancelToken,
) -> (Vec<Scene>, Result<DecodeSummary, Interrupt<DecodeError>>) {
    let mut sink = Collected::default();
    let result = decoder.decode_file(&BytesSource(bytes), context, &mut sink, cancel);
    (sink.scenes, result)
}
