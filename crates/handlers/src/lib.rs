//! The one registry of decoders and playlist resolvers.
//!
//! The built-ins come from their crates; installed plugins are added behind the same type. Everything that picks
//! a decoder or resolver by id or by file name asks a [`Handlers`], never the crates' own lists.

use std::{
    path::Path,
    sync::{Arc, PoisonError, RwLock},
};

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

    /// These handlers plus `resolvers`, after the built-ins, in the order given.
    #[must_use]
    pub fn with_resolvers(
        mut self,
        resolvers: impl IntoIterator<Item = Arc<dyn PlaylistResolver>>,
    ) -> Self {
        self.resolvers.extend(resolvers);
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

    /// The decoders that may read the file called `path`, in registry order: the candidates to offer when the
    /// user has not named one. A decoder named explicitly is taken whatever its extensions say.
    ///
    /// A decoder with no extensions may read any file. Otherwise one of its extensions must match the lowercased
    /// file name: an extension with a single dot (`.json`) must equal the name's final suffix, as Python's
    /// `Path.suffix` finds it (none for `.json` alone or `name.`); a compound extension (`.tar.gz`), which only a
    /// decoder that declares it can be found by, must end the name.
    pub fn decoders_for(&self, path: &Path) -> Vec<&Arc<dyn Decoder>> {
        let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
            return Vec::new();
        };
        let name = name.to_lowercase();
        self.decoders
            .iter()
            .filter(|decoder| {
                let extensions = &decoder.spec().extensions;
                extensions.is_empty()
                    || extensions
                        .iter()
                        .any(|extension| matches_extension(&name, extension))
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

/// Whether the lowercased file `name` has this lowercase `extension` (see [`Handlers::decoders_for`]).
fn matches_extension(name: &str, extension: &str) -> bool {
    if extension.matches('.').count() > 1 {
        return name.ends_with(extension);
    }
    name.rfind('.')
        .filter(|dot| *dot > 0 && dot + 1 < name.len())
        .is_some_and(|dot| &name[dot..] == extension)
}

/// The [`Handlers`] the app uses right now, replaced as plugins are installed, removed or edited. Work takes one
/// snapshot with [`current`](Self::current) and keeps it, so what is already running never changes underneath it.
pub struct HandlerSet(RwLock<Arc<Handlers>>);

impl HandlerSet {
    /// A set that starts with `handlers`.
    pub fn new(handlers: Handlers) -> Arc<Self> {
        Arc::new(Self(RwLock::new(Arc::new(handlers))))
    }

    /// The handlers as they are now.
    pub fn current(&self) -> Arc<Handlers> {
        Arc::clone(&self.0.read().unwrap_or_else(PoisonError::into_inner))
    }

    /// Makes `handlers` what [`current`](Self::current) answers from now on.
    pub fn replace(&self, handlers: Handlers) {
        *self.0.write().unwrap_or_else(PoisonError::into_inner) = Arc::new(handlers);
    }

    /// A function that finds decoders by id in whatever the set holds when it is called.
    pub fn decoder_lookup(
        self: &Arc<Self>,
    ) -> impl Fn(&str) -> Option<Arc<dyn Decoder>> + Send + Sync + 'static {
        let set = Arc::clone(self);
        move |id| set.current().decoder(id).cloned()
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use foundation::{CancelToken, Interrupt};
    use handler_api::{
        Cli, DecodeError, DecodeSummary, FieldKind, Form, Input, SceneSink, Spec, Values,
    };

    use super::*;

    fn assert_well_formed(who: &str, form: &Form) {
        let keys: HashSet<_> = form.fields.iter().map(|field| &field.key).collect();
        assert_eq!(keys.len(), form.fields.len(), "{who}: unique keys");
        for field in &form.fields {
            if let FieldKind::Scan { depends_on, .. } = &field.kind {
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

    /// A decoder that is only listed, with the given extensions.
    struct Reads(Spec);

    impl Decoder for Reads {
        fn spec(&self) -> &Spec {
            &self.0
        }

        fn decode(
            &self,
            _input: &mut dyn Input,
            _options: &Values,
            _sink: &mut dyn SceneSink,
            _cancel: &CancelToken,
        ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
            Err(Interrupt::Cancelled)
        }
    }

    #[test]
    fn candidates_are_found_by_final_suffix_or_declared_compound_and_no_extensions_reads_any_file()
    {
        let like = |id: &str, extensions: &[&str]| -> Arc<dyn Decoder> {
            let mot = Handlers::built_in()
                .decoder("MOT_FILE")
                .unwrap()
                .spec()
                .clone();
            Arc::new(Reads(Spec {
                id: id.into(),
                extensions: extensions.iter().map(|&e| e.to_owned()).collect(),
                ..mot
            }))
        };
        let handlers =
            Handlers::built_in().with_decoders([like("ANY", &[]), like("ARCHIVE", &[".tar.gz"])]);
        let found = |name: &str| -> Vec<String> {
            handlers
                .decoders_for(Path::new(name))
                .iter()
                .map(|decoder| decoder.spec().id.clone())
                .collect()
        };

        // The wildcard comes after the built-ins, in registry order.
        assert_eq!(found("gt.TXT"), ["MOT_FILE", "ANY"]);
        assert_eq!(found("video.mp4"), ["ANY"]);
        assert_eq!(found("a.TAR.GZ"), ["ANY", "ARCHIVE"]);
        // Only the final suffix counts for a simple extension, and a name that is all extension has none.
        assert_eq!(found("gt.txt.bak"), ["ANY"]);
        assert_eq!(found(".txt"), ["ANY"]);
        // A named decoder is taken without looking at its extensions.
        assert!(handlers.decoder("MOT_FILE").is_some());
    }
}
