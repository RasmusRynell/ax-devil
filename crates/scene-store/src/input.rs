//! The host's framing of an overlay file into the records a decoder reads.

use std::{cell::Cell, path::Path};

use foundation::{CancelToken, Interrupt};
use handler_api::{DecodeError, Framing, Input, Record};

use crate::source::LazySource;

/// Lines between two checks of the cancel token.
const CANCEL_EVERY: u64 = 4096;

/// The records of one source file: the whole file as one record, or its lines, split on `\n`, trimmed, blank ones
/// skipped and the rest numbered from 1 by their place in the file. The bytes are read when the first record is
/// asked for, so a decoder that takes the path never loads the file.
pub(crate) struct FileInput<'a> {
    source: &'a LazySource,
    framing: Framing,
    /// Where the next line starts; past the end once the last line was read.
    next_start: usize,
    /// The number of the last line read.
    line: u64,
    /// How many records were given. The ingest's sink reads it, so a Scene is placed by the record being read.
    records: &'a Cell<u64>,
}

impl<'a> FileInput<'a> {
    pub(crate) fn new(source: &'a LazySource, framing: Framing, records: &'a Cell<u64>) -> Self {
        Self {
            source,
            framing,
            next_start: 0,
            line: 0,
            records,
        }
    }
}

impl Input for FileInput<'_> {
    fn path(&self) -> Option<&Path> {
        Some(self.source.path())
    }

    fn next(&mut self, cancel: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        let bytes = self.source.bytes(cancel)?;
        let (bytes, position) = match self.framing {
            Framing::Whole if self.records.get() == 0 => (bytes, 0),
            Framing::Whole => return Ok(None),
            Framing::Records => loop {
                if self.next_start > bytes.len() {
                    return Ok(None);
                }
                let rest = &bytes[self.next_start..];
                let end = memchr::memchr(b'\n', rest).unwrap_or(rest.len());
                let line = rest[..end].trim_ascii();
                self.next_start += end + 1;
                self.line += 1;
                if self.line.is_multiple_of(CANCEL_EVERY) {
                    cancel.check()?;
                }
                if !line.is_empty() {
                    break (line, self.line);
                }
            },
        };
        self.records.set(self.records.get() + 1);
        Ok(Some(Record {
            bytes,
            position,
            capture_time: None,
        }))
    }
}
