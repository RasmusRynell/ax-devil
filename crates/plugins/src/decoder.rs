//! A plugin's decoder handler as a [`Decoder`]: the handler the rest of the app picks by extension or id.

use std::{path::PathBuf, sync::Arc};

use foundation::{CancelToken, Interrupt};
use handler_api::{DecodeError, DecodeSummary, Decoder, Framing, Input, SceneSink, Spec, Values};
use scene::{Coverage, Fields, FilterPolicy};
use serde_json::json;

use crate::{
    PROTOCOL,
    manifest::DecoderHandler,
    program::Program,
    run::{self, End},
};

/// A decoder that runs an installed plugin's process.
pub struct PluginDecoder {
    program: Arc<Program>,
    spec: Spec,
}

impl PluginDecoder {
    /// The decoder for `handler`, which `program`'s plugin declares.
    pub fn new(program: Arc<Program>, handler: &DecoderHandler) -> Self {
        let spec = Spec {
            id: handler.id.clone(),
            plugin: program.manifest().id.clone(),
            name: handler.name.clone(),
            extensions: handler.extensions.clone(),
            framing: Framing::Whole,
            bases: handler.bases.clone(),
            default_basis: handler.default_basis,
            options: handler.options.clone(),
            version: 1,
        };
        Self { program, spec }
    }

    /// The program this handler shares with the rest of its plugin.
    pub fn program(&self) -> &Arc<Program> {
        &self.program
    }
}

impl Decoder for PluginDecoder {
    fn spec(&self) -> &Spec {
        &self.spec
    }

    /// A plugin whose files cannot be hashed has no identity (`None`), and then [`decode`](Decoder::decode)
    /// refuses to run it, so nothing is built or cached under a missing identity.
    fn code_hash(&self) -> Option<[u8; 32]> {
        self.program.code_hash().ok()
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
        self.program
            .code_hash()
            .map_err(|error| failed(format!("The plugin's files cannot be read: {error}")))?;
        let Some(file) = input.path() else {
            return Err(failed(
                "A plugin decoder reads a file, not a live source".to_owned(),
            ));
        };
        // The plugin works in a folder of its own, so the path it is given must not depend on ours.
        let overlay: PathBuf = std::path::absolute(file)
            .map_err(|error| failed(format!("Cannot resolve the overlay path: {error}")))?;
        let Some(overlay_text) = overlay.to_str() else {
            return Err(failed(
                "The overlay path is not valid UTF-8, which plugins need".to_owned(),
            ));
        };
        let manifest = self.program.manifest();
        let Some((command, args)) = manifest.command.split_first() else {
            return Err(failed("The plugin has no command".to_owned()));
        };
        let process = run::Spec {
            id: &self.spec.id,
            program: command,
            args,
            env: &manifest.env,
            request: json!({
                "protocol": PROTOCOL,
                "op": "decode",
                "handler": self.spec.id,
                "input": { "path": overlay_text },
                "options": options.declared(),
            })
            .to_string(),
            idle_timeout: manifest.idle_timeout,
        };
        let outcome = run::run(&process, sink, cancel);
        match outcome.end {
            End::Complete => Ok(match outcome.header {
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
            }),
            // The host knows how many samples it took: an error after some is an incomplete lane, before any a
            // failed one.
            End::Incomplete(reason) => Err(failed(reason)),
            End::Stopped => Err(Interrupt::Cancelled),
        }
    }
}
