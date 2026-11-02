//! Built-in Scene decoders: overlay files and records turned into Scenes.
//!
//! A decoder reads a whole file ([`FileSceneDecoder`]) or one self-contained record at a time
//! ([`RecordSceneDecoder`]). These traits are not the playback crate's video decoders.

mod cvat;
mod iso8601;
mod lines;
mod mot;
mod onvif_xml;
mod uvg_vcm;
mod warning;

use std::{ops::ControlFlow, path::Path};

use foundation::{CancelToken, FrameSize, Interrupt};
use scene::{Coverage, Fields, FilterPolicy, Scene};

pub use warning::{LoadWarning, SkippedRecords};

/// How a decoder's output is kept between runs.
///
/// PLACEHOLDER(indexed store)
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Storage {
    /// Decoded once into a cache that is rebuilt when `artifact_version` changes.
    DerivedCache,
    /// Read straight from the source file through an index of its records.
    SourceIndexed {
        /// Whether any record decodes without its neighbours.
        independent_records: bool,
    },
}

/// What the host needs to know about a decoder without running it.
#[derive(Debug, PartialEq, Eq)]
pub struct DecoderSpec {
    /// The id of the plugin the decoder ships as; workspaces and the CLI name it.
    pub plugin_id: &'static str,
    /// The handler id that selects the decoder.
    pub handler: &'static str,
    /// What the user reads.
    pub display_name: &'static str,
    /// File extensions the decoder reads: lowercase, with the dot.
    pub extensions: &'static [&'static str],
    /// Changes whenever the decoder's output for the same file changes.
    pub artifact_version: u32,
    /// How the output is kept.
    pub storage: Storage,
}

/// Options for one decode, as the user or a playlist resolver gave them.
///
/// PLACEHOLDER(D4 schema): resolvers declare their settings as a schema, but each decoder still parses these by hand.
pub type DecodeOptions = serde_json::Map<String, serde_json::Value>;

/// Why a whole file could not be decoded: words for the user.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DecodeError(pub String);

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DecodeError {}

/// What the host passes to one decode: the user's options and the video the overlay belongs to.
#[derive(Clone, Copy, Debug)]
pub struct DecodeContext<'a> {
    /// The options the user or a playlist resolver gave.
    pub options: &'a DecodeOptions,
    /// The decoded size of the video's frames, which pixel coordinates are normalized by.
    pub frame_size: FrameSize,
}

/// What a finished decode reports besides its Scenes.
#[derive(Clone, Debug, PartialEq)]
pub struct DecodeSummary {
    /// Problems that did not stop the decode.
    pub warnings: Vec<LoadWarning>,
    /// The class filters this file's content calls for.
    pub filter: FilterPolicy,
    /// What the file says about itself, kept with the track by the scene store; empty when the decoder reads none.
    pub metadata: Fields,
    /// What the file says about frames it has no Scene for.
    pub coverage: Coverage,
}

/// What a whole-file decoder reads: built-ins take the bytes, a converter takes the path. Implemented by the store.
pub trait SourceFile {
    /// Where the file is.
    fn path(&self) -> &Path;

    /// The whole file, read once through the handle the store fingerprinted; later calls return the same bytes.
    fn bytes(&self, cancel: &CancelToken) -> Result<&[u8], Interrupt<DecodeError>>;
}

/// How a sample with the start key of an earlier one joins it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OnEqualKey {
    /// The sample's contents are added to what the key already holds.
    Merge,
    /// The sample replaces what the key held.
    Replace,
}

/// What a file says about itself; given early it lets the host publish samples while the decode runs.
#[derive(Clone, Debug, PartialEq)]
pub struct Header {
    /// The class filters this file's content calls for.
    pub filter: FilterPolicy,
    /// What the file says about frames it has no Scene for.
    pub coverage: Coverage,
    /// What the file says about itself, kept with the track by the scene store.
    pub metadata: Fields,
}

/// Where every decoder puts its Scenes; the host groups, orders, stores and publishes them.
pub trait SceneSink {
    /// At most once, before the first sample. Without it the host publishes at the end. A decoder that calls it
    /// returns the same values in its [`DecodeSummary`].
    fn header(&mut self, header: Header);

    /// Takes one Scene. `Break` means the host cannot take more (store full or failed, or the decode was
    /// cancelled): the decoder stops at once and returns what it has, which the host ignores.
    fn sample(&mut self, scene: Scene, on_equal_key: OnEqualKey) -> ControlFlow<()>;

    /// A problem found while decoding, shown to the user. A warning goes here or into the [`DecodeSummary`], not
    /// both; the host shows both.
    fn warn(&mut self, warning: LoadWarning);
}

/// A decoder that reads a whole file.
pub trait FileSceneDecoder: Send + Sync {
    /// What the host needs to know about the decoder.
    fn spec(&self) -> &'static DecoderSpec;

    /// SHA-256 of the code that makes the Scenes when that code is not part of the app, such as a converter's
    /// manifest and files. It is part of the identity of the scene cache: another hash rebuilds. `None` for built-ins.
    fn code_hash(&self) -> Option<[u8; 32]> {
        None
    }

    /// `options` with the decoder's defaults filled in and undeclared keys dropped, so that an explicit default and
    /// an omitted key make one scene cache. The error is words for the user.
    fn declared_options(&self, options: &DecodeOptions) -> Result<DecodeOptions, DecodeError> {
        Ok(options.clone())
    }

    /// Decodes `source`, handing each Scene to `sink`. Frame-keyed decoders give ascending frames, one Scene per
    /// frame; time-keyed ones give them in record order.
    fn decode_file(
        &self,
        source: &dyn SourceFile,
        context: &DecodeContext<'_>,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>>;
}

/// A decoder whose records stand alone.
pub trait RecordSceneDecoder: Send + Sync {
    /// What the host needs to know about the decoder.
    fn spec(&self) -> &'static DecoderSpec;

    /// Decodes one trimmed, non-empty record. `Ok(None)` means the record has no overlay; `Err` skips the record
    /// and carries the reason.
    fn decode_record(&self, record: &[u8]) -> Result<Option<Scene>, String>;

    /// The class filters for this decoder's content.
    fn filter_policy(&self) -> FilterPolicy;
}

static FILE_DECODERS: [&dyn FileSceneDecoder; 4] = [
    &mot::MotDecoder,
    &uvg_vcm::UvgVcmDecoder,
    &onvif_xml::OnvifXmlDecoder,
    &cvat::CvatDecoder,
];
static RECORD_DECODERS: [&dyn RecordSceneDecoder; 1] = [&onvif_xml::OnvifXmlDecoder];

/// Every built-in whole-file decoder.
pub fn file_decoders() -> &'static [&'static dyn FileSceneDecoder] {
    &FILE_DECODERS
}

/// Every built-in record decoder.
pub fn record_decoders() -> &'static [&'static dyn RecordSceneDecoder] {
    &RECORD_DECODERS
}

/// The whole-file decoder with this handler id.
pub fn file_decoder(handler: &str) -> Option<&'static dyn FileSceneDecoder> {
    file_decoders()
        .iter()
        .copied()
        .find(|decoder| decoder.spec().handler == handler)
}

/// The record decoder with this handler id.
pub fn record_decoder(handler: &str) -> Option<&'static dyn RecordSceneDecoder> {
    record_decoders()
        .iter()
        .copied()
        .find(|decoder| decoder.spec().handler == handler)
}
