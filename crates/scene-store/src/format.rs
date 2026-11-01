//! The artifact file: header, Scene records, then a tail that says what the records are.
//!
//! See the crate README for the layout and the reasons for it.

use std::{fs::File, io::Write};

use overlay_sync::SampleKeys;
use serde::{Deserialize, Serialize};

use crate::{io::read_exact_at, stat::Stat};

/// Changes when the layout of an artifact file changes; older files are rebuilt.
pub const STORE_FORMAT_VERSION: u32 = 1;

const MAGIC: &[u8; 4] = b"AXSC";
const END: &[u8; 4] = b"AXSE";
/// Magic and format version.
pub(crate) const HEADER_LEN: u64 = 8;
/// Three section offsets, the tail checksum and the end mark.
const FOOTER_LEN: u64 = 32;

/// The most bytes the record index of an artifact may take (about ten million samples); a larger file is not cached.
const MAX_INDEX_LEN: u64 = 256 << 20;
/// The most bytes the summary (warnings, filter policy, metadata) may take.
const MAX_SUMMARY_LEN: u64 = 64 << 20;
/// The most bytes the identity may take.
const MAX_IDENTITY_LEN: u64 = 1 << 20;
/// The most bytes one Scene record may take. Reading one allocates its whole length, so a damaged index cannot ask
/// for more.
pub(crate) const MAX_RECORD_LEN: u64 = 256 << 20;

/// Everything that must match for an artifact to be used again, besides the source's content.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct Identity {
    pub handler: String,
    pub artifact_version: u32,
    pub scene_major: u16,
    pub scene_minor: u16,
    pub options: String,
    pub width: u32,
    pub height: u32,
    /// The source's stat when the artifact was built or last republished.
    pub stat: Stat,
    /// SHA-256 of the source's content; absent when the source changed while it was being decoded, which makes the
    /// artifact unusable.
    pub digest: Option<[u8; 32]>,
}

impl Identity {
    /// Whether both were made by the same decode, ignoring the source's stat and digest.
    pub(crate) fn same_decode(&self, other: &Identity) -> bool {
        (
            &self.handler,
            self.artifact_version,
            self.scene_major,
            self.scene_minor,
        ) == (
            &other.handler,
            other.artifact_version,
            other.scene_major,
            other.scene_minor,
        ) && (&self.options, self.width, self.height) == (&other.options, other.width, other.height)
    }
}

/// The sample keys of an artifact, on the file's clock.
#[derive(Serialize, Deserialize)]
pub(crate) enum Keys {
    Frames(Vec<u64>),
    Times(Vec<i64>),
}

impl Keys {
    fn len(&self) -> usize {
        match self {
            Self::Frames(keys) => keys.len(),
            Self::Times(keys) => keys.len(),
        }
    }

    fn is_ascending(&self) -> bool {
        match self {
            Self::Frames(keys) => keys.windows(2).all(|pair| pair[0] < pair[1]),
            Self::Times(keys) => keys.windows(2).all(|pair| pair[0] < pair[1]),
        }
    }
}

/// Where each Scene record is and which sample it belongs to.
#[derive(Serialize, Deserialize)]
pub(crate) struct Index {
    pub keys: Keys,
    /// Record `r` occupies `boundaries[r]..boundaries[r + 1]`; one more entry than there are records.
    pub boundaries: Vec<u64>,
    /// CRC-32 of each record.
    pub crcs: Vec<u32>,
    /// The record of each sample when the records are not in key order; empty when sample `i` is record `i`.
    pub order: Vec<u32>,
}

/// An [`Index`] that has been checked against the file it came from, so reading by it cannot go out of bounds.
pub(crate) struct Layout {
    pub keys: SampleKeys,
    pub boundaries: Vec<u64>,
    pub crcs: Vec<u32>,
    pub order: Vec<u32>,
}

impl Index {
    /// Checks the index against the end of the record area and turns it into a [`Layout`].
    pub(crate) fn into_layout(self, records_end: u64) -> Result<Layout, String> {
        let records = self.crcs.len();
        let well_formed = self.boundaries.len() == records + 1
            && self.boundaries.first() == Some(&HEADER_LEN)
            && self.boundaries.last() == Some(&records_end)
            && self
                .boundaries
                .windows(2)
                .all(|pair| pair[0] <= pair[1] && pair[1] - pair[0] <= MAX_RECORD_LEN)
            && self.keys.is_ascending();
        if !well_formed {
            return Err("the record index is inconsistent".to_string());
        }
        let samples = self.keys.len();
        let order_ok = if self.order.is_empty() {
            samples == records
        } else {
            self.order.len() == samples
                && self.order.iter().all(|&record| (record as usize) < records)
        };
        if !order_ok {
            return Err("the sample order is inconsistent".to_string());
        }
        let keys = match self.keys {
            Keys::Frames(keys) => SampleKeys::Frames(keys.into()),
            Keys::Times(keys) => {
                SampleKeys::Times(keys.into_iter().map(foundation::MediaTime).collect())
            }
        };
        Ok(Layout {
            keys,
            boundaries: self.boundaries,
            crcs: self.crcs,
            order: self.order,
        })
    }
}

/// The sections after the records, read and checksummed but not yet parsed.
pub(crate) struct Tail {
    bytes: Vec<u8>,
    index_len: usize,
    summary_len: usize,
    /// Where the records end and the tail begins.
    pub records_end: u64,
}

impl Tail {
    /// Reads the header and the tail of `file`; the reason is why the file is not a usable artifact.
    pub(crate) fn read(file: &File) -> Result<Self, String> {
        let io = |e: std::io::Error| e.to_string();
        let len = file.metadata().map_err(io)?.len();
        if len < HEADER_LEN + FOOTER_LEN {
            return Err("the file is too short".to_string());
        }
        let mut header = [0_u8; HEADER_LEN as usize];
        read_exact_at(file, &mut header, 0).map_err(io)?;
        if &header[..4] != MAGIC {
            return Err("it is not a scene artifact".to_string());
        }
        let version = u32::from_le_bytes([header[4], header[5], header[6], header[7]]);
        if version != STORE_FORMAT_VERSION {
            return Err(format!("format version {version}"));
        }
        let mut footer = [0_u8; FOOTER_LEN as usize];
        read_exact_at(file, &mut footer, len - FOOTER_LEN).map_err(io)?;
        if &footer[28..] != END {
            return Err("the end mark is missing".to_string());
        }
        let word = |at: usize| {
            let mut eight = [0_u8; 8];
            eight.copy_from_slice(&footer[at..at + 8]);
            u64::from_le_bytes(eight)
        };
        let (records_end, summary_at, identity_at) = (word(0), word(8), word(16));
        let checksum = u32::from_le_bytes([footer[24], footer[25], footer[26], footer[27]]);
        let tail_end = len - FOOTER_LEN;
        if !(HEADER_LEN <= records_end
            && records_end <= summary_at
            && summary_at <= identity_at
            && identity_at <= tail_end)
        {
            return Err("the section offsets are inconsistent".to_string());
        }
        let (index_len, summary_len, identity_len) = (
            summary_at - records_end,
            identity_at - summary_at,
            tail_end - identity_at,
        );
        if index_len > MAX_INDEX_LEN
            || summary_len > MAX_SUMMARY_LEN
            || identity_len > MAX_IDENTITY_LEN
        {
            return Err("a section is larger than any artifact writes".to_string());
        }
        // The sections are bounded above, so the sum fits; the allocation can still fail and then discards the file.
        let size = usize::try_from(tail_end - records_end).map_err(|e| e.to_string())?;
        let mut bytes = Vec::new();
        bytes.try_reserve_exact(size).map_err(|e| e.to_string())?;
        bytes.resize(size, 0);
        read_exact_at(file, &mut bytes, records_end).map_err(io)?;
        if crc32fast::hash(&bytes) != checksum {
            return Err("the tail checksum does not match".to_string());
        }
        Ok(Self {
            index_len: index_len as usize,
            summary_len: summary_len as usize,
            bytes,
            records_end,
        })
    }

    /// Who the artifact was built for.
    pub(crate) fn identity(&self) -> Result<Identity, String> {
        let bytes = &self.bytes[self.index_len + self.summary_len..];
        postcard::from_bytes(bytes).map_err(|e| e.to_string())
    }

    /// The record index and what the file reported besides its Scenes, as the bytes they are stored in.
    pub(crate) fn index_and_summary(&self) -> (&[u8], &[u8]) {
        let (index, rest) = self.bytes.split_at(self.index_len);
        (index, &rest[..self.summary_len])
    }
}

/// Writes the tail and footer after the records that end at `records_end`.
pub(crate) fn write_tail(
    out: &mut impl Write,
    records_end: u64,
    index: &[u8],
    summary: &[u8],
    identity: &Identity,
) -> std::io::Result<()> {
    let identity = postcard::to_stdvec(identity).map_err(std::io::Error::other)?;
    if index.len() as u64 > MAX_INDEX_LEN
        || summary.len() as u64 > MAX_SUMMARY_LEN
        || identity.len() as u64 > MAX_IDENTITY_LEN
    {
        return Err(std::io::Error::other(
            "the file has more samples or metadata than the scene cache can hold",
        ));
    }
    let summary_at = records_end + index.len() as u64;
    let identity_at = summary_at + summary.len() as u64;
    let mut checksum = crc32fast::Hasher::new();
    for section in [index, summary, &identity] {
        checksum.update(section);
        out.write_all(section)?;
    }
    for word in [records_end, summary_at, identity_at] {
        out.write_all(&word.to_le_bytes())?;
    }
    out.write_all(&checksum.finalize().to_le_bytes())?;
    out.write_all(END)
}

/// Writes the file header.
pub(crate) fn write_header(out: &mut impl Write) -> std::io::Result<()> {
    out.write_all(MAGIC)?;
    out.write_all(&STORE_FORMAT_VERSION.to_le_bytes())
}
