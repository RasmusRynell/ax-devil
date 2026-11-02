//! A converter as a [`FileSceneDecoder`]: the handler the rest of the app picks by extension or id.

use std::{
    path::{Path, PathBuf},
    sync::Mutex,
};

use foundation::{CancelToken, Interrupt};
use scene::{Coverage, Fields, FilterPolicy};
use scene_decoders::{
    DecodeContext, DecodeError, DecodeOptions, DecodeSummary, DecoderSpec, FileSceneDecoder,
    LoadWarning, SceneSink, SourceFile, Storage,
};
use serde_json::{Value, json};

use crate::{
    PROTOCOL, code,
    manifest::{Manifest, ManifestError},
    run::{self, End, Spec},
};

/// A file decoder that runs an installed converter process.
pub struct ConverterDecoder {
    manifest: Manifest,
    spec: &'static DecoderSpec,
    /// The files the identity hashes, as last seen, with their hash.
    code: Mutex<Option<(Vec<code::CodeFile>, [u8; 32])>>,
}

impl ConverterDecoder {
    /// The decoder for a validated manifest.
    ///
    /// The [`DecoderSpec`] must be `'static`, so its text is leaked: once per converter loaded, which the
    /// registry bounds.
    pub fn new(manifest: Manifest) -> Self {
        let extensions: Vec<&'static str> = manifest
            .extensions
            .iter()
            .map(|extension| &*Box::leak(extension.clone().into_boxed_str()))
            .collect();
        let leak = |text: &str| -> &'static str { Box::leak(text.to_owned().into_boxed_str()) };
        let spec = Box::leak(Box::new(DecoderSpec {
            plugin_id: leak(&manifest.id),
            handler: leak(&manifest.id),
            display_name: leak(&manifest.name),
            extensions: Box::leak(extensions.into_boxed_slice()),
            artifact_version: 1,
            storage: Storage::DerivedCache,
            bases: Box::leak(manifest.bases.clone().into_boxed_slice()),
            default_basis: manifest.default_basis,
        }));
        Self {
            manifest,
            spec,
            code: Mutex::new(None),
        }
    }

    /// Reads the manifest at `path` and makes its decoder.
    pub fn load(path: &Path) -> Result<Self, ManifestError> {
        Manifest::load(path).map(Self::new)
    }

    /// What the converter declares about itself.
    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }

    /// The identity of the converter's code: SHA-256 of the manifest as it was loaded (the one that runs) and the files it names, which the scene store
    /// keeps next to an artifact so an edited converter rebuilds. Files are read again only when their size or
    /// modification time changed since the last call.
    pub fn code_hash(&self) -> Result<[u8; 32], ManifestError> {
        let files = code::collect(&self.manifest, code::LIMITS)?;
        let mut memo = self
            .code
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Some((seen, hash)) = &*memo
            && *seen == files
        {
            return Ok(*hash);
        }
        let hash = code::digest(&self.manifest, &files)
            .map_err(|error| ManifestError::new("code", error.to_string()))?;
        *memo = Some((files, hash));
        Ok(hash)
    }

    /// The options the converter receives for `given`: its declared keys only, defaults filled in. An explicit
    /// default and an omitted key give the same result, so they share an artifact.
    pub fn declared_options(&self, given: &DecodeOptions) -> Result<DecodeOptions, DecodeError> {
        let mut options = DecodeOptions::new();
        for field in &self.manifest.options {
            let value = match given.get(&field.key) {
                None | Some(Value::Null) => field.default.clone(),
                Some(Value::String(text)) => Some(text.clone()),
                Some(_) => {
                    return Err(DecodeError(format!(
                        "The option '{}' must be text",
                        field.label
                    )));
                }
            };
            match value {
                Some(value) if !(field.required && value.is_empty()) => {
                    options.insert(field.key.clone(), Value::String(value));
                }
                _ if field.required => {
                    return Err(DecodeError(format!(
                        "The option '{}' is required",
                        field.label
                    )));
                }
                _ => {}
            }
        }
        Ok(options)
    }

    fn undeclared(&self, given: &DecodeOptions) -> Option<LoadWarning> {
        let keys: Vec<&String> = given
            .keys()
            .filter(|key| !self.manifest.options.iter().any(|field| &field.key == *key))
            .collect();
        (!keys.is_empty()).then(|| LoadWarning {
            problem: format!("{} does not take some of the options", self.manifest.name),
            reasons: keys.iter().take(3).map(|key| format!("'{key}'")).collect(),
            consequence: "They were not passed to it.".to_owned(),
            more_reasons: keys.len() > 3,
        })
    }
}

impl FileSceneDecoder for ConverterDecoder {
    fn spec(&self) -> &'static DecoderSpec {
        self.spec
    }

    /// A converter whose files cannot be hashed has no identity (`None`), and then [`decode_file`] refuses to run
    /// it, so nothing is built or cached under a missing identity.
    ///
    /// [`decode_file`]: FileSceneDecoder::decode_file
    fn code_hash(&self) -> Option<[u8; 32]> {
        ConverterDecoder::code_hash(self).ok()
    }

    fn declared_options(
        &self,
        _path: &Path,
        options: &DecodeOptions,
    ) -> Result<DecodeOptions, DecodeError> {
        ConverterDecoder::declared_options(self, options)
    }

    fn decode_file(
        &self,
        source: &dyn SourceFile,
        context: &DecodeContext<'_>,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        cancel.check()?;
        let failed = |reason: String| Interrupt::Failed(DecodeError(reason));
        ConverterDecoder::code_hash(self)
            .map_err(|error| failed(format!("The converter's files cannot be read: {error}")))?;
        let options = self
            .declared_options(context.options)
            .map_err(Interrupt::Failed)?;
        if let Some(warning) = self.undeclared(context.options) {
            sink.warn(warning);
        }
        // The converter works in a folder of its own, so the path it is given must not depend on ours.
        let overlay: PathBuf = std::path::absolute(source.path())
            .map_err(|error| failed(format!("Cannot resolve the overlay path: {error}")))?;
        let Some(overlay_text) = overlay.to_str() else {
            return Err(failed(
                "The overlay path is not valid UTF-8, which converters need".to_owned(),
            ));
        };
        let Some((program, args)) = self.manifest.command.split_first() else {
            return Err(failed("The converter has no command".to_owned()));
        };
        let spec = Spec {
            id: self.spec.handler,
            program,
            args,
            env: &self.manifest.env,
            overlay: &overlay,
            request: json!({
                "protocol": PROTOCOL,
                "path": overlay_text,
                "options": options,
            })
            .to_string(),
            idle_timeout: self.manifest.idle_timeout,
        };
        let outcome = run::run(&spec, sink, cancel);
        match outcome.end {
            End::Complete => {
                let header = outcome.header;
                Ok(match header {
                    Some(header) => DecodeSummary {
                        warnings: Vec::new(),
                        filter: header.filter,
                        metadata: header.metadata,
                        coverage: header.coverage,
                    },
                    None => DecodeSummary {
                        warnings: Vec::new(),
                        filter: FilterPolicy {
                            name: self.spec.handler,
                            options: Vec::new(),
                        },
                        metadata: Fields::new(),
                        coverage: Coverage::Sparse,
                    },
                })
            }
            // The host knows how many samples it took: an error after some is an incomplete lane, before any a
            // failed one.
            End::Incomplete(reason) => Err(failed(reason)),
            End::Stopped => Err(Interrupt::Cancelled),
        }
    }
}
