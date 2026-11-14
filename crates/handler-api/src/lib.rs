//! What a handler is: the contracts of the decoders that turn overlay files and records into Scenes, and of the
//! playlist resolvers that turn settings into playlists, with the one settings schema ([`Form`]) both declare.
//!
//! Handlers know nothing of the host: it frames the input ([`Input`]), validates the options ([`Form::validate`]),
//! stores what a decoder emits ([`SceneSink`]) and shows its warnings. Built-ins and installed plugins implement
//! the same traits.

mod bundle;
mod decoder;
mod form;
mod placement;
mod refs;
mod resolver;
mod skipped;

pub use bundle::{Bundle, BundleLane, BundleVideo};
pub use decoder::{
    DecodeError, DecodeSummary, Decoder, Framing, Header, Input, OnEqualKey, Record, SceneSink,
    Spec,
};
pub use form::{Cli, Field, FieldKind, Form, ScanCount, Selection, Values, invalid_field};
pub use placement::{On, Place};
pub use refs::{ImageSequence, LaneSource, OverlayRef, VideoRef};
pub use resolver::{
    Choice, PlaylistResolver, ResolvedEntry, ResolvedPlaylist, ResolvedPlaylists, ResolverSpec,
};
pub use skipped::{SkippedRecords, shorten};

/// Settings or options as the user, a playlist or a workspace holds them: not yet checked against a [`Form`].
pub type Settings = serde_json::Map<String, serde_json::Value>;

/// Visits every type this crate sends to the frontend, for `export-types`.
pub fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<Place>();
    types.visit::<On>();
}
