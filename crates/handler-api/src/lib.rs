//! What a handler is: the contracts of the decoders that turn overlay files and records into Scenes, and of the
//! playlist resolvers that turn settings into playlists, with the one settings schema ([`Form`]) both declare.
//!
//! Handlers know nothing of the host: it frames the input ([`Input`]), validates the options ([`Form::validate`]),
//! stores what a decoder emits ([`SceneSink`]) and shows its warnings. Built-ins and installed plugins implement
//! the same traits.

mod decoder;
mod form;
mod refs;
mod resolver;
mod skipped;

pub use decoder::{
    DecodeError, DecodeSummary, Decoder, Framing, Header, Input, OnEqualKey, Record, SceneSink,
    Spec,
};
pub use form::{Cli, Field, FieldKind, Form, Selection, Values, invalid_field};
pub use refs::{ImageSequence, OverlayRef, VideoRef};
pub use resolver::{
    Choice, PlaylistResolver, ResolvedEntry, ResolvedPlaylist, ResolvedPlaylists, ResolverSpec,
};
pub use skipped::SkippedRecords;

/// Settings or options as the user, a playlist or a workspace holds them: not yet checked against a [`Form`].
pub type Settings = serde_json::Map<String, serde_json::Value>;
