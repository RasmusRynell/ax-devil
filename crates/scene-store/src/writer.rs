//! Writing an artifact: Scenes are encoded as they are given and dropped, so memory holds one record and its place.

use std::io::Write;

use scene::{Scene, serial};

use crate::{
    format::{
        HEADER_LEN, Identity, Index, Keys, Layout, MAX_RECORD_LEN, Sections, write_header,
        write_tail,
    },
    summary::Summary,
};

/// What the writer returns when the file is complete.
pub(crate) struct Finished<W> {
    pub out: W,
    pub layout: Layout,
}

/// Appends Scene records to an artifact being built. The caller decides which sample each record is.
pub(crate) struct RecordWriter<W: Write> {
    out: W,
    position: u64,
    boundaries: Vec<u64>,
    crcs: Vec<u32>,
    scratch: Vec<u8>,
}

impl<W: Write> RecordWriter<W> {
    /// Starts an artifact on `out` by writing the header.
    pub(crate) fn new(mut out: W) -> std::io::Result<Self> {
        write_header(&mut out)?;
        Ok(Self {
            out,
            position: HEADER_LEN,
            boundaries: vec![HEADER_LEN],
            crcs: Vec::new(),
            scratch: Vec::new(),
        })
    }

    /// Appends one Scene as the next record and returns the record's number. The reason is why the file cannot
    /// become an artifact.
    pub(crate) fn add(&mut self, scene: &Scene) -> Result<u32, String> {
        let record = u32::try_from(self.crcs.len())
            .map_err(|_| "the file has too many samples".to_string())?;
        self.scratch.clear();
        serial::encode(scene, &mut self.scratch).map_err(|e| e.to_string())?;
        if self.scratch.len() as u64 > MAX_RECORD_LEN {
            return Err("a Scene is too large for the scene cache".to_string());
        }
        self.out
            .write_all(&self.scratch)
            .map_err(|e| format!("cannot write the scene cache: {e}"))?;
        self.position += self.scratch.len() as u64;
        self.boundaries.push(self.position);
        self.crcs.push(crc32fast::hash(&self.scratch));
        Ok(record)
    }

    /// Makes every record added so far readable through another handle of the file.
    pub(crate) fn flush(&mut self) -> Result<(), String> {
        self.out
            .flush()
            .map_err(|e| format!("cannot write the scene cache: {e}"))
    }

    /// The records written so far.
    pub(crate) fn records(&self) -> usize {
        self.crcs.len()
    }

    /// Where record `record` is and its checksum: `(start, end, crc)`.
    pub(crate) fn span(&self, record: u32) -> Option<(u64, u64, u32)> {
        let at = record as usize;
        Some((
            *self.boundaries.get(at)?,
            *self.boundaries.get(at + 1)?,
            *self.crcs.get(at)?,
        ))
    }

    /// The boundaries and checksums of the records, from record `from` on.
    pub(crate) fn tail_from(&self, from: usize) -> (&[u64], &[u32]) {
        (&self.boundaries[from + 1..], &self.crcs[from..])
    }

    /// Writes the tail for the samples `keys` (one per record, ascending) and flushes.
    pub(crate) fn finish(
        mut self,
        keys: Keys,
        summary: &Summary,
        identity: &Identity,
        history: &[u8],
    ) -> Result<Finished<W>, String> {
        let records_end = self.position;
        let index = Index {
            keys,
            boundaries: self.boundaries,
            crcs: self.crcs,
        };
        let index_bytes = postcard::to_stdvec(&index).map_err(|e| e.to_string())?;
        let summary_bytes = postcard::to_stdvec(summary).map_err(|e| e.to_string())?;
        let identity_bytes = identity.to_bytes().map_err(|e| e.to_string())?;
        write_tail(
            &mut self.out,
            records_end,
            Sections {
                index: &index_bytes,
                summary: &summary_bytes,
                identity: &identity_bytes,
                history,
            },
        )
        .and_then(|()| self.out.flush())
        .map_err(|e| format!("cannot write the scene cache: {e}"))?;
        Ok(Finished {
            layout: index.into_layout(records_end)?,
            out: self.out,
        })
    }
}
