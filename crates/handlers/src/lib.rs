//! The one registry of file decoders, record decoders and playlist resolvers.
//!
//! The built-ins come from their crates; a plugin runtime adds entries behind the same type later. Everything that
//! picks a decoder or resolver by id or by file name asks a [`Handlers`], never the crates' own registries.

use std::{path::Path, sync::Arc};

use foundation::{CancelToken, Cancelled, Interrupt, UserError};
use playlist_resolvers::{
    Choice, PlaylistResolver, ResolvedPlaylist, ResolverSchema, ResolverSpec, Settings, WatchPaths,
};
use scene::Scene;
use scene_decoders::{
    DecodeContext, DecodeError, DecodeOptions, DecodeSummary, DecoderSpec, FileSceneDecoder,
    RecordSceneDecoder, SceneSink, SourceFile,
};

/// The decoders and resolvers this build knows. Cheap to clone.
#[derive(Clone)]
pub struct Handlers {
    file_decoders: Vec<Arc<dyn FileSceneDecoder>>,
    record_decoders: Vec<Arc<dyn RecordSceneDecoder>>,
    resolvers: Vec<Arc<dyn PlaylistResolver>>,
}

impl Handlers {
    /// Every built-in decoder and resolver.
    pub fn built_in() -> Self {
        Self {
            file_decoders: scene_decoders::file_decoders()
                .iter()
                .map(|&decoder| Arc::new(BuiltInFile(decoder)) as Arc<dyn FileSceneDecoder>)
                .collect(),
            record_decoders: scene_decoders::record_decoders()
                .iter()
                .map(|&decoder| Arc::new(BuiltInRecord(decoder)) as Arc<dyn RecordSceneDecoder>)
                .collect(),
            resolvers: playlist_resolvers::resolvers()
                .iter()
                .map(|&resolver| Arc::new(BuiltInResolver(resolver)) as Arc<dyn PlaylistResolver>)
                .collect(),
        }
    }

    /// These handlers plus `decoders`, after the built-ins, in the order given.
    #[must_use]
    pub fn with_file_decoders(
        mut self,
        decoders: impl IntoIterator<Item = Arc<dyn FileSceneDecoder>>,
    ) -> Self {
        self.file_decoders.extend(decoders);
        self
    }

    /// The whole-file decoders in registry order.
    pub fn file_decoders(&self) -> &[Arc<dyn FileSceneDecoder>] {
        &self.file_decoders
    }

    /// The whole-file decoder with this handler id.
    pub fn file_decoder(&self, handler: &str) -> Option<&Arc<dyn FileSceneDecoder>> {
        self.file_decoders
            .iter()
            .find(|decoder| decoder.spec().handler == handler)
    }

    /// The decoders whose declared extensions end the file name (case-insensitive), in registry order.
    pub fn file_decoders_for(&self, path: &Path) -> Vec<&Arc<dyn FileSceneDecoder>> {
        let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
            return Vec::new();
        };
        let name = name.to_lowercase();
        self.file_decoders
            .iter()
            .filter(|decoder| {
                decoder
                    .spec()
                    .extensions
                    .iter()
                    .any(|extension| name.ends_with(extension))
            })
            .collect()
    }

    /// The record decoders in registry order.
    pub fn record_decoders(&self) -> &[Arc<dyn RecordSceneDecoder>] {
        &self.record_decoders
    }

    /// The playlist resolvers in registry order.
    pub fn resolvers(&self) -> &[Arc<dyn PlaylistResolver>] {
        &self.resolvers
    }

    /// The playlist resolver with this id.
    pub fn resolver(&self, id: &str) -> Option<&Arc<dyn PlaylistResolver>> {
        self.resolvers
            .iter()
            .find(|resolver| resolver.spec().id == id)
    }
}

/// A built-in file decoder, shared like any other.
struct BuiltInFile(&'static dyn FileSceneDecoder);

impl FileSceneDecoder for BuiltInFile {
    fn spec(&self) -> &'static DecoderSpec {
        self.0.spec()
    }

    fn declared_options(
        &self,
        path: &Path,
        options: &DecodeOptions,
    ) -> Result<DecodeOptions, DecodeError> {
        self.0.declared_options(path, options)
    }

    fn decode_file(
        &self,
        source: &dyn SourceFile,
        context: &DecodeContext<'_>,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        self.0.decode_file(source, context, sink, cancel)
    }
}

/// A built-in record decoder, shared like any other.
struct BuiltInRecord(&'static dyn RecordSceneDecoder);

impl RecordSceneDecoder for BuiltInRecord {
    fn spec(&self) -> &'static DecoderSpec {
        self.0.spec()
    }

    fn decode_record(&self, record: &[u8]) -> Result<Option<Scene>, String> {
        self.0.decode_record(record)
    }

    fn filter_policy(&self) -> scene::FilterPolicy {
        self.0.filter_policy()
    }
}

/// A built-in playlist resolver, shared like any other.
struct BuiltInResolver(&'static dyn PlaylistResolver);

impl PlaylistResolver for BuiltInResolver {
    fn spec(&self) -> &'static ResolverSpec {
        self.0.spec()
    }

    fn schema(&self) -> &'static ResolverSchema {
        self.0.schema()
    }

    fn scan(
        &self,
        field: &str,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<Vec<Choice>, Interrupt<UserError>> {
        self.0.scan(field, settings, cancel)
    }

    fn resolve(
        &self,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<ResolvedPlaylist, Interrupt<UserError>> {
        self.0.resolve(settings, cancel)
    }

    fn watch_paths(
        &self,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<WatchPaths, Cancelled> {
        self.0.watch_paths(settings, cancel)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn built_ins_are_found_by_id_and_by_the_end_of_a_file_name() {
        let handlers = Handlers::built_in();
        let handler = |path: &str| -> Vec<&str> {
            handlers
                .file_decoders_for(Path::new(path))
                .iter()
                .map(|decoder| decoder.spec().handler)
                .collect()
        };

        assert!(handlers.file_decoder("MOT_FILE").is_some());
        assert!(handlers.file_decoder("NOPE").is_none());
        assert!(handlers.resolver("mot_challenge").is_some());
        assert_eq!(handler("/data/gt/GT.TXT"), ["MOT_FILE"]);
        assert!(handler("/data/video.mp4").is_empty());
        assert!(!handlers.record_decoders().is_empty());
        assert_eq!(handlers.resolvers().len(), 2);
    }
}
