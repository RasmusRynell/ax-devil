//! A converter as a [`Decoder`]: the handler the rest of the app picks by extension or id.

use std::{
    path::{Path, PathBuf},
    sync::Mutex,
};

use foundation::{CancelToken, Interrupt};
use handler_api::{DecodeError, DecodeSummary, Decoder, Framing, Input, SceneSink, Spec, Values};
use scene::{Coverage, Fields, FilterPolicy};
use serde_json::json;

use crate::{
    PROTOCOL, code,
    manifest::{Manifest, ManifestError},
    run::{self, End},
};

/// A decoder that runs an installed converter process.
pub struct ConverterDecoder {
    manifest: Manifest,
    spec: Spec,
    /// The files the identity hashes, as last seen, with their hash.
    code: Mutex<Option<(Vec<code::CodeFile>, [u8; 32])>>,
}

impl ConverterDecoder {
    /// The decoder for a validated manifest.
    pub fn new(manifest: Manifest) -> Self {
        let spec = Spec {
            id: manifest.id.clone(),
            plugin: manifest.id.clone(),
            name: manifest.name.clone(),
            extensions: manifest.extensions.clone(),
            framing: Framing::Whole,
            bases: manifest.bases.clone(),
            default_basis: manifest.default_basis,
            options: manifest.options.clone(),
            version: 1,
        };
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
}

impl Decoder for ConverterDecoder {
    fn spec(&self) -> &Spec {
        &self.spec
    }

    /// A converter whose files cannot be hashed has no identity (`None`), and then [`decode`](Decoder::decode)
    /// refuses to run it, so nothing is built or cached under a missing identity.
    fn code_hash(&self) -> Option<[u8; 32]> {
        ConverterDecoder::code_hash(self).ok()
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        cancel.check()?;
        let failed = |reason: String| Interrupt::Failed(DecodeError(reason));
        ConverterDecoder::code_hash(self)
            .map_err(|error| failed(format!("The converter's files cannot be read: {error}")))?;
        let Some(file) = input.path() else {
            return Err(failed(
                "A converter reads a file, not a live source".to_owned(),
            ));
        };
        // The converter works in a folder of its own, so the path it is given must not depend on ours.
        let overlay: PathBuf = std::path::absolute(file)
            .map_err(|error| failed(format!("Cannot resolve the overlay path: {error}")))?;
        let Some(overlay_text) = overlay.to_str() else {
            return Err(failed(
                "The overlay path is not valid UTF-8, which converters need".to_owned(),
            ));
        };
        let Some((program, args)) = self.manifest.command.split_first() else {
            return Err(failed("The converter has no command".to_owned()));
        };
        let process = run::Spec {
            id: &self.spec.id,
            program,
            args,
            env: &self.manifest.env,
            overlay: &overlay,
            request: json!({
                "protocol": PROTOCOL,
                "path": overlay_text,
                "options": options.declared(),
            })
            .to_string(),
            idle_timeout: self.manifest.idle_timeout,
        };
        let outcome = run::run(&process, sink, cancel);
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
                            name: crate::records::POLICY_NAME,
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
