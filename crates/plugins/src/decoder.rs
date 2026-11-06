//! A plugin's decoder handler as a [`Decoder`]: the handler the rest of the app picks by extension or id.

use std::{path::PathBuf, sync::Arc};

use base64::{Engine, engine::general_purpose::STANDARD};
use foundation::{CancelToken, Interrupt};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Framing, Header, Input, SceneSink, Spec, Values, shorten,
};
use scene::{Coverage, Fields, FilterPolicy, MatchBasis};
use serde_json::json;

use crate::{
    PROTOCOL,
    manifest::{DecoderHandler, DecoderInput},
    program::Program,
    records::Records,
    run::{End, Process, Pumped},
};

/// A decoder that runs an installed plugin's process.
pub struct PluginDecoder {
    program: Arc<Program>,
    input: DecoderInput,
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
            description: handler.description.clone(),
            framing: match handler.input {
                DecoderInput::Path => Framing::Whole,
                DecoderInput::Records => Framing::Records,
            },
            bases: handler.bases.clone(),
            default_basis: handler.default_basis,
            options: handler.options.clone(),
            version: 2,
        };
        Self {
            program,
            input: handler.input,
            spec,
        }
    }

    /// The program this handler shares with the rest of its plugin.
    pub fn program(&self) -> &Arc<Program> {
        &self.program
    }

    /// One run over the file the input names; the plugin reads it itself.
    fn run_path(
        &self,
        input: &dyn Input,
        options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<(End, Option<Header>), DecodeError> {
        let Some(file) = input.path() else {
            return Err(DecodeError(
                "A plugin decoder that reads a file cannot take a live source".to_owned(),
            ));
        };
        // The plugin works in a folder of its own, so the path it is given must not depend on ours.
        let overlay: PathBuf = std::path::absolute(file)
            .map_err(|error| DecodeError(format!("Cannot resolve the overlay path: {error}")))?;
        let Some(overlay_text) = overlay.to_str() else {
            return Err(DecodeError(
                "The overlay path is not valid UTF-8, which plugins need".to_owned(),
            ));
        };
        let request = json!({
            "protocol": PROTOCOL,
            "op": "decode",
            "handler": self.spec.id,
            "input": { "path": overlay_text },
            "options": options.declared(),
        });
        let mut records = Records::new(sink);
        let end = self
            .program
            .run(&self.spec.id, &request, &mut records, cancel);
        Ok((end, records.finish()))
    }

    /// One process per session, started with the first record and fed one record at a time: the next goes out when
    /// the plugin has answered the last. A process that ends after it answered something is started again with the
    /// next record, and the record it did not answer is skipped; one that ends before its first answer fails the
    /// session, so a plugin that cannot run is not started once per document.
    fn run_records(
        &self,
        input: &mut dyn Input,
        options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<(End, Option<Header>), Interrupt<DecodeError>> {
        let request = json!({
            "protocol": PROTOCOL,
            "op": "decode",
            "handler": self.spec.id,
            "input": "records",
            "options": options.declared(),
        })
        .to_string();
        let mut records = Records::new(sink);
        let mut process: Option<Process> = None;
        let mut answered = false;
        let end = loop {
            let Some(record) = input.next(cancel)? else {
                break self.close(process.take(), &mut records, cancel);
            };
            let (position, line) = (record.position, record_line(record.bytes));
            records.at(position);
            match self.answer(&mut process, &request, line, &mut records, cancel) {
                Ok(()) => answered = true,
                Err(End::Incomplete(reason)) if answered => {
                    let reason = shorten(&reason.replace('\n', " / "));
                    records.skip_record(format!(
                        "the plugin stopped ({reason}); it starts again with the next record"
                    ));
                }
                Err(end) => break end,
            }
        };
        Ok((end, records.finish()))
    }

    /// Sends `line` to the plugin, starting it first if it is not running, and reads its answer into `records`.
    /// `Err` is how the process ended without answering; it is gone then.
    fn answer(
        &self,
        process: &mut Option<Process>,
        request: &str,
        line: String,
        records: &mut Records<'_>,
        cancel: &CancelToken,
    ) -> Result<(), End> {
        let mut running = match process.take() {
            Some(running) => running,
            None => {
                let mut started = self.program.start(&self.spec.id).map_err(End::Incomplete)?;
                started.send(request.to_owned());
                records.new_process();
                started
            }
        };
        running.send(line);
        match running.pump(records, cancel) {
            Pumped::Done => {
                *process = Some(running);
                Ok(())
            }
            Pumped::Ended => Err(match running.finish(None) {
                End::Complete => End::Incomplete("The plugin exited without answering".to_owned()),
                other => other,
            }),
            Pumped::Stop(stop) => Err(running.finish(Some(stop))),
        }
    }

    /// The records are over: lets the plugin see the end of its input and exit by itself.
    fn close(
        &self,
        process: Option<Process>,
        records: &mut Records<'_>,
        cancel: &CancelToken,
    ) -> End {
        let Some(mut process) = process else {
            return End::Complete;
        };
        process.close_input();
        let stop = loop {
            match process.pump(records, cancel) {
                Pumped::Done => {}
                Pumped::Ended => break None,
                Pumped::Stop(stop) => break Some(stop),
            }
        };
        process.finish(stop)
    }
}

/// A record as the plugin gets it: its text when it is UTF-8, otherwise its bytes in base64.
fn record_line(bytes: &[u8]) -> String {
    let record = match std::str::from_utf8(bytes) {
        Ok(text) => json!({ "text": text }),
        Err(_) => json!({ "base64": STANDARD.encode(bytes) }),
    };
    json!({ "record": record }).to_string()
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

    /// A live track keys by time, so the decoder must read records and be able to key by it.
    fn reads_live(&self) -> bool {
        self.spec.framing == Framing::Records && self.spec.bases.contains(&MatchBasis::Time)
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
        let (end, header) = match self.input {
            DecoderInput::Path => self
                .run_path(input, options, sink, cancel)
                .map_err(Interrupt::Failed)?,
            DecoderInput::Records => self.run_records(input, options, sink, cancel)?,
        };
        match end {
            End::Complete => Ok(match header {
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
