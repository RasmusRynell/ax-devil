//! A decoder: records in, Scenes out.

use std::{ops::ControlFlow, path::Path};

use foundation::{CancelToken, Interrupt, MediaTime, Warning};
use scene::{Coverage, Fields, FilterPolicy, MatchBasis, Scene};

use crate::{Form, Values};

/// How the host cuts an overlay file into the records a decoder reads.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Framing {
    /// The whole file is one record: CVAT, MOT, UVG-VCM and the converters.
    Whole,
    /// One record per line: ONVIF, ADF. Blank lines are skipped and the rest are trimmed.
    Records,
}

/// What the host needs to know about a decoder without running it.
///
/// Decoders know nothing of the video: they emit records, and the host matches them to frames by the lane's
/// [`MatchBasis`]. `bases` are the ones the decoder's records support; `default_basis` is the one the host starts
/// with. For a time-keyed decoder the Frame key of a record is its position in the file (record k, frame k).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Spec {
    /// The decoder id that selects it; workspaces and the CLI name it.
    pub id: String,
    /// The id of the plugin the decoder ships as.
    pub plugin: String,
    /// What the user reads.
    pub name: String,
    /// File extensions the decoder reads: lowercase, with the dot.
    pub extensions: Vec<String>,
    /// How the host frames the file for it.
    pub framing: Framing,
    /// The bases a lane may match this decoder's records by.
    pub bases: Vec<MatchBasis>,
    /// The recommended basis, one of `bases`.
    pub default_basis: MatchBasis,
    /// The options the decoder takes.
    pub options: Form,
    /// Changes whenever the decoder's output for the same file changes.
    pub version: u32,
}

/// One record of the input.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Record<'a> {
    /// The record's bytes.
    pub bytes: &'a [u8],
    /// Where the record is in the input, from 1: the line number of a line, 0 for a whole file.
    pub position: u64,
    /// When the source captured the record, if it says.
    pub capture_time: Option<MediaTime>,
}

/// The records of one overlay file, as the host frames them.
pub trait Input {
    /// The file the records come from; `None` for a live source. A decoder that reads the file itself (a converter)
    /// takes this and asks for no records.
    fn path(&self) -> Option<&Path>;

    /// The next record, or `None` at the end. The record is valid until the next call.
    fn next(&mut self, cancel: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>>;
}

/// Why a whole file could not be decoded: words for the user.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DecodeError(pub String);

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DecodeError {}

/// What a finished decode reports besides its Scenes.
#[derive(Clone, Debug, PartialEq)]
pub struct DecodeSummary {
    /// Problems that did not stop the decode.
    pub warnings: Vec<Warning>,
    /// The class filters this file's content calls for.
    pub filter: FilterPolicy,
    /// What the file says about itself, kept with the track by the scene store; empty when the decoder reads none.
    pub metadata: Fields,
    /// What the file says about frames it has no Scene for.
    pub coverage: Coverage,
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

    /// The record at `position` (see [`Record::position`]) could not be decoded. The host counts these and shows one
    /// warning naming the first few reasons, so a [`Framing::Records`] decoder reports each bad record here.
    fn skip(&mut self, position: u64, reason: String);

    /// A problem found while decoding, shown to the user. A warning goes here or into the [`DecodeSummary`], not
    /// both; the host shows both.
    fn warn(&mut self, warning: Warning);
}

/// Turns the records of an overlay file into Scenes.
pub trait Decoder: Send + Sync {
    /// What the host needs to know about the decoder.
    fn spec(&self) -> &Spec;

    /// SHA-256 of the code that makes the Scenes when that code is not part of the app, such as a converter's
    /// manifest and files. It is part of the identity of the scene cache: another hash rebuilds. `None` for built-ins.
    fn code_hash(&self) -> Option<[u8; 32]> {
        None
    }

    /// Reads `input`, handing each Scene to `sink`. Frame-keyed decoders give ascending frames, one Scene per
    /// frame; time-keyed ones give them in record order. `options` fit [`Spec::options`].
    fn decode(
        &self,
        input: &mut dyn Input,
        options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>>;
}
