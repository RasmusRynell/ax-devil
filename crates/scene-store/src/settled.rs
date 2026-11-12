//! The end of a decode: storing the artifact and showing the last snapshot.

use std::{fs::File, io::BufWriter};

use foundation::{CancelToken, ErrorCode, Interrupt, UserError, Warning};
use handler_api::{DecodeSummary, Header};

use crate::{
    format::{Identity, Layout},
    history::{HistoryBuilder, Slot},
    keylog::KeyLog,
    link::{Link, Presented},
    output::Output,
    source::LazySource,
    summary::{Restored, Summary},
    track::{Decided, Ingest as State, Meta},
    writer::RecordWriter,
};

/// An ingest whose Scenes are in key order in one artifact, ready to be shown.
pub(crate) struct Settled {
    pub(crate) writer: RecordWriter<BufWriter<File>>,
    pub(crate) keys: KeyLog,
    pub(crate) history: HistoryBuilder,
    pub(crate) history_bytes: usize,
    pub(crate) output: Output,
    /// The records readers already have of this artifact.
    pub(crate) known: usize,
    /// Whether the artifact numbers the samples anew.
    pub(crate) fresh: bool,
    pub(crate) replaced: usize,
    pub(crate) header: Option<Header>,
    pub(crate) warnings: Vec<Warning>,
    pub(crate) cancel: CancelToken,
}

impl Settled {
    /// Stores the artifact and shows every sample as final.
    pub(crate) fn complete(
        mut self,
        link: &mut Link,
        summary: DecodeSummary,
        source: &LazySource,
        mut identity: Identity,
    ) -> Result<(), Interrupt<UserError>> {
        let internal = |reason: String| Interrupt::Failed(link.error(ErrorCode::Internal, reason));
        let mut decoded = summary;
        if let Some(header) = self.header.take() {
            decoded.filter = header.filter;
            decoded.coverage = header.coverage;
            decoded.metadata = header.metadata;
        }
        decoded.warnings.append(&mut self.warnings);
        let mut stored = Summary::of(&decoded).map_err(internal)?;
        if self.replaced > 0 {
            stored.warn(&Warning {
                problem: format!(
                    "{} samples were replaced by a later one with the same time",
                    self.replaced
                ),
                reasons: Vec::new(),
                consequence: "Only the last sample of each time is shown.".to_string(),
                more_reasons: false,
            });
        }
        // A source that changed while it was read has a digest that no longer describes it: keep the artifact for
        // this session but never accept it again.
        let digest = source.digest(&self.cancel)?;
        identity.digest = source.unchanged().then_some(digest);
        self.cancel.check()?;
        let built = self.history.finish(Some(&decoded.filter));
        let section = built.section(self.history_bytes).map_err(internal)?;
        let held = section.is_none();
        if held {
            identity.digest = None;
        }
        let history = section.unwrap_or_default();
        let finished = self
            .writer
            .finish(self.keys.all(), &stored, &identity, &history)
            .map_err(internal)?;
        drop(finished.out);
        let target = self.output.target().map(std::path::Path::to_path_buf);
        let file = self.output.publish();
        let restored = stored.restore().map_err(internal)?;
        let Layout {
            keys,
            boundaries,
            crcs,
        } = finished.layout;
        let Restored {
            coverage,
            warnings,
            filter,
            metadata,
        } = restored;
        link.present(
            Presented {
                file,
                keys,
                records: (&boundaries[self.known + 1..], &crcs[self.known..]),
                warnings,
                ingest: State::Complete,
                decided: Decided::All,
                stored: target,
                fresh: self.fresh,
                history: Some(if held {
                    built.held()
                } else {
                    Slot::initial(&State::Complete)
                }),
            },
            move || Meta {
                coverage,
                filter,
                metadata,
            },
        );
        Ok(())
    }

    /// Shows the samples that arrived as final and the ingest as incomplete. Nothing is stored.
    pub(crate) fn incomplete(
        mut self,
        link: &mut Link,
        error: UserError,
    ) -> Result<(), Interrupt<UserError>> {
        let internal = |reason: String| Interrupt::Failed(link.error(ErrorCode::Internal, reason));
        self.writer.flush().map_err(internal)?;
        let file = self.output.handle().map_err(|e| {
            Interrupt::Failed(link.error(
                ErrorCode::Internal,
                format!("Cannot read the scene cache: {e}"),
            ))
        })?;
        let keys = self.keys.publish();
        let header = self.header.take();
        let history = self
            .history
            .finish(header.as_ref().map(|h| &h.filter))
            .held();
        link.present(
            Presented {
                file,
                keys,
                records: self.writer.tail_from(self.known),
                warnings: std::mem::take(&mut self.warnings),
                ingest: State::Incomplete(error),
                decided: Decided::All,
                stored: None,
                fresh: self.fresh,
                history: Some(history),
            },
            || Meta::of(header.as_ref()),
        );
        Ok(())
    }
}
