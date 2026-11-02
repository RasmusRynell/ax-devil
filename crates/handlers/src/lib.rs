//! The one registry of decoders and playlist resolvers.
//!
//! The built-ins come from their crates; installed converters are added behind the same type. Everything that picks
//! a decoder or resolver by id or by file name asks a [`Handlers`], never the crates' own lists.

use std::{path::Path, sync::Arc};

use handler_api::{Decoder, PlaylistResolver};

/// The decoders and resolvers this build knows. Cheap to clone.
#[derive(Clone)]
pub struct Handlers {
    decoders: Vec<Arc<dyn Decoder>>,
    resolvers: Vec<Arc<dyn PlaylistResolver>>,
}

impl Handlers {
    /// Every built-in decoder and resolver.
    pub fn built_in() -> Self {
        Self {
            decoders: Vec::new(),
            resolvers: playlist_resolvers::resolvers(),
        }
        .with_decoders(scene_decoders::decoders())
    }

    /// These handlers plus `decoders`, after the built-ins, in the order given.
    ///
    /// # Panics
    ///
    /// When a decoder's default basis is not one of its bases: that is a defect of the decoder, not of its input.
    #[must_use]
    pub fn with_decoders(mut self, decoders: impl IntoIterator<Item = Arc<dyn Decoder>>) -> Self {
        for decoder in decoders {
            let spec = decoder.spec();
            assert!(
                spec.bases.contains(&spec.default_basis),
                "{}: the default basis is not one of its bases",
                spec.id
            );
            self.decoders.push(decoder);
        }
        self
    }

    /// The decoders in registry order.
    pub fn decoders(&self) -> &[Arc<dyn Decoder>] {
        &self.decoders
    }

    /// The decoder with this id.
    pub fn decoder(&self, id: &str) -> Option<&Arc<dyn Decoder>> {
        self.decoders.iter().find(|decoder| decoder.spec().id == id)
    }

    /// A function that finds decoders by id, for hosts that must not depend on the registry.
    pub fn decoder_lookup(
        &self,
    ) -> impl Fn(&str) -> Option<Arc<dyn Decoder>> + Send + Sync + 'static {
        let handlers = self.clone();
        move |id| handlers.decoder(id).cloned()
    }

    /// The decoders whose declared extensions end the file name (case-insensitive), in registry order.
    pub fn decoders_for(&self, path: &Path) -> Vec<&Arc<dyn Decoder>> {
        let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
            return Vec::new();
        };
        let name = name.to_lowercase();
        self.decoders
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

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use handler_api::{Cli, FieldKind, Form};

    use super::*;

    fn assert_well_formed(who: &str, form: &Form) {
        let keys: HashSet<_> = form.fields.iter().map(|field| &field.key).collect();
        assert_eq!(keys.len(), form.fields.len(), "{who}: unique keys");
        for field in &form.fields {
            if let FieldKind::Scan { depends_on } = &field.kind {
                assert!(
                    !depends_on.is_empty()
                        && depends_on
                            .iter()
                            .all(|key| *key != field.key && keys.contains(key)),
                    "{who}: {} scans from other fields",
                    field.key
                );
            }
            if let FieldKind::DecoderOptions { handler_field } = &field.kind {
                assert!(
                    form.fields.iter().any(
                        |other| other.key == *handler_field && other.kind == FieldKind::Handler
                    ),
                    "{who}: {} takes the options of a Handler field",
                    field.key
                );
            }
            if field.cli == Cli::Positional {
                assert!(field.required, "{who}: a positional is required");
            }
        }
    }

    #[test]
    fn built_ins_are_declared_consistently_and_found_by_id_and_by_the_end_of_a_file_name() {
        let handlers = Handlers::built_in();
        let ids: HashSet<_> = handlers
            .decoders()
            .iter()
            .map(|d| d.spec().id.clone())
            .collect();
        assert_eq!(
            ids.len(),
            handlers.decoders().len(),
            "decoder ids are unique"
        );
        for decoder in handlers.decoders() {
            let spec = decoder.spec();
            assert!(
                !spec.extensions.is_empty(),
                "{} reads no extension",
                spec.id
            );
            for extension in &spec.extensions {
                assert!(
                    extension.starts_with('.') && *extension == extension.to_lowercase(),
                    "{}: extension {extension:?} must be lowercase with a dot",
                    spec.id
                );
            }
            assert_well_formed(&spec.id, &spec.options);
            assert_eq!(
                handlers.decoder(&spec.id).map(|found| found.spec()),
                Some(spec)
            );
        }
        for resolver in handlers.resolvers() {
            assert_well_formed(&resolver.spec().id, &resolver.spec().form);
        }

        let decoders_for = |path: &str| -> Vec<String> {
            handlers
                .decoders_for(Path::new(path))
                .iter()
                .map(|decoder| decoder.spec().id.clone())
                .collect()
        };
        assert!(handlers.decoder("NOPE").is_none());
        assert!(handlers.resolver("mot_challenge").is_some());
        assert_eq!(decoders_for("/data/gt/GT.TXT"), ["MOT_FILE"]);
        assert!(decoders_for("/data/video.mp4").is_empty());
        assert_eq!(handlers.resolvers().len(), 2);
    }
}
