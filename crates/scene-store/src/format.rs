//! The artifact file: header, Scene records, then a tail that says what the records are.
//!
//! See the crate README for the layout and the reasons for it.

use std::{fs::File, io::Write};

use overlay_sync::{FrameKey, KeyChunks, SampleKeys};
use scene::{Scene, serial};
use serde::{Deserialize, Serialize};

use crate::{io::read_exact_at, stat::Stat};

/// Changes when the layout of an artifact file changes; older files are rebuilt.
pub const STORE_FORMAT_VERSION: u32 = 5;

const MAGIC: &[u8; 4] = b"AXSC";
const END: &[u8; 4] = b"AXSE";
/// Magic and format version.
pub(crate) const HEADER_LEN: u64 = 8;
/// Four section offsets, the tail checksum and the end mark.
const FOOTER_LEN: u64 = 40;

/// The most bytes the record index of an artifact may take (about ten million samples); a larger file is not cached.
const MAX_INDEX_LEN: u64 = 256 << 20;
/// The most bytes the summary (warnings, filter policy, metadata) may take.
const MAX_SUMMARY_LEN: u64 = 64 << 20;
/// The most bytes the identity may take.
const MAX_IDENTITY_LEN: u64 = 1 << 20;
/// The most bytes the file history may take: the history cap, with room for its own framing.
const MAX_HISTORY_LEN: u64 = (256 << 20) + (1 << 20);
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
    /// SHA-256 of the code of a decoder that is not part of the app (a converter's manifest and files); `None` for
    /// built-ins.
    pub code: Option<[u8; 32]>,
    /// The source's stat when the artifact was built or last republished.
    pub stat: Stat,
    /// SHA-256 of the source's content; absent when the source changed while it was being decoded, which makes the
    /// artifact unusable.
    pub digest: Option<[u8; 32]>,
}

impl Identity {
    /// The stored form.
    pub(crate) fn to_bytes(&self) -> std::io::Result<Vec<u8>> {
        postcard::to_stdvec(self).map_err(std::io::Error::other)
    }

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
        ) && (&self.options, self.code) == (&other.options, other.code)
    }
}

/// The sample keys of an artifact: frame numbers, timestamps, or both, with sample `i` at index `i` in each. The
/// timestamps ascend; the frame numbers are unique but, for a timestamp-keyed file, in file order.
#[derive(Serialize, Deserialize)]
pub(crate) struct Keys {
    pub frames: Option<Vec<u64>>,
    pub times: Option<Vec<i64>>,
}

impl Keys {
    /// How many samples there are; `None` when the two lists disagree.
    fn len(&self) -> Option<usize> {
        match (&self.frames, &self.times) {
            (Some(frames), Some(times)) => (frames.len() == times.len()).then_some(frames.len()),
            (Some(keys), None) => Some(keys.len()),
            (None, Some(keys)) => Some(keys.len()),
            (None, None) => Some(0),
        }
    }

    fn times_ascend(&self) -> bool {
        self.times.iter().flatten().is_sorted_by(|a, b| a < b)
    }

    /// The frame keys in frame order, or `None` when two samples share a frame number.
    fn frame_keys(frames: Vec<u64>) -> Option<KeyChunks<FrameKey>> {
        let mut keys: Vec<FrameKey> = frames
            .iter()
            .enumerate()
            .map(|(sample, &frame)| {
                Some(FrameKey {
                    frame,
                    sample: u32::try_from(sample).ok()?,
                })
            })
            .collect::<Option<_>>()?;
        keys.sort_unstable_by_key(|key| key.frame);
        keys.windows(2)
            .all(|pair| pair[0].frame < pair[1].frame)
            .then(|| keys.into())
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
}

/// An [`Index`] that has been checked against the file it came from, so reading by it cannot go out of bounds.
pub(crate) struct Layout {
    pub keys: SampleKeys,
    pub boundaries: Vec<u64>,
    pub crcs: Vec<u32>,
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
            && self.keys.times_ascend();
        if !well_formed {
            return Err("the record index is inconsistent".to_string());
        }
        if self.keys.len() != Some(records) {
            return Err("the sample count is inconsistent".to_string());
        }
        let frames = match self.keys.frames {
            Some(frames) => Some(
                Keys::frame_keys(frames)
                    .ok_or_else(|| "two samples have one frame number".to_string())?,
            ),
            None => None,
        };
        let keys = SampleKeys {
            frames,
            times: self
                .keys
                .times
                .map(|times| times.into_iter().map(foundation::MediaTime).collect()),
        };
        Ok(Layout {
            keys,
            boundaries: self.boundaries,
            crcs: self.crcs,
        })
    }
}

/// The sections after the records, read and checksummed but not yet parsed.
pub(crate) struct Tail {
    bytes: Vec<u8>,
    index_len: usize,
    summary_len: usize,
    identity_len: usize,
    /// Where the records end and the tail begins.
    pub records_end: u64,
}

impl Tail {
    /// Reads the header and the tail of `file`; the reason is why the file is not a usable artifact.
    pub(crate) fn read(file: &File) -> Result<Self, String> {
        let io = |e: std::io::Error| e.to_string();
        let footer = Footer::read(file)?;
        let Footer {
            records_end,
            summary_at,
            identity_at,
            history_at,
            tail_end,
            checksum,
        } = footer;
        let (index_len, summary_len, identity_len) = (
            summary_at - records_end,
            identity_at - summary_at,
            history_at - identity_at,
        );
        if index_len > MAX_INDEX_LEN
            || summary_len > MAX_SUMMARY_LEN
            || identity_len > MAX_IDENTITY_LEN
            || tail_end - history_at > MAX_HISTORY_LEN
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
            identity_len: identity_len as usize,
            bytes,
            records_end,
        })
    }

    /// Who the artifact was built for.
    pub(crate) fn identity(&self) -> Result<Identity, String> {
        postcard::from_bytes(self.sections().identity).map_err(|e| e.to_string())
    }

    /// The sections as the bytes they are stored in.
    pub(crate) fn sections(&self) -> Sections<'_> {
        let (index, rest) = self.bytes.split_at(self.index_len);
        let (summary, rest) = rest.split_at(self.summary_len);
        let (identity, history) = rest.split_at(self.identity_len);
        Sections {
            index,
            summary,
            identity,
            history,
        }
    }
}

/// The offsets the footer of an artifact gives, checked against each other and the file's length.
struct Footer {
    records_end: u64,
    summary_at: u64,
    identity_at: u64,
    history_at: u64,
    /// Where the footer begins.
    tail_end: u64,
    /// CRC-32 of the sections between the records and the footer.
    checksum: u32,
}

impl Footer {
    fn read(file: &File) -> Result<Self, String> {
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
        if &footer[36..] != END {
            return Err("the end mark is missing".to_string());
        }
        let word = |at: usize| {
            let mut eight = [0_u8; 8];
            eight.copy_from_slice(&footer[at..at + 8]);
            u64::from_le_bytes(eight)
        };
        let (records_end, summary_at, identity_at, history_at) =
            (word(0), word(8), word(16), word(24));
        let checksum = u32::from_le_bytes([footer[32], footer[33], footer[34], footer[35]]);
        let tail_end = len - FOOTER_LEN;
        if !(HEADER_LEN <= records_end
            && records_end <= summary_at
            && summary_at <= identity_at
            && identity_at <= history_at
            && history_at <= tail_end)
        {
            return Err("the section offsets are inconsistent".to_string());
        }
        Ok(Self {
            records_end,
            summary_at,
            identity_at,
            history_at,
            tail_end,
            checksum,
        })
    }
}

/// Reads the history section of `file` alone, without the rest of the tail. The section carries a checksum of its
/// own, which the caller checks.
pub(crate) fn read_history(file: &File) -> Result<Vec<u8>, String> {
    let footer = Footer::read(file)?;
    let len = footer.tail_end - footer.history_at;
    if len > MAX_HISTORY_LEN {
        return Err("a section is larger than any artifact writes".to_string());
    }
    let size = usize::try_from(len).map_err(|e| e.to_string())?;
    let mut bytes = Vec::new();
    bytes.try_reserve_exact(size).map_err(|e| e.to_string())?;
    bytes.resize(size, 0);
    read_exact_at(file, &mut bytes, footer.history_at).map_err(|e| e.to_string())?;
    Ok(bytes)
}

/// The sections of a tail as stored: the record index, what the file reported besides its Scenes, who the artifact
/// was built for, and the file history ([`crate::history`]).
#[derive(Clone, Copy)]
pub(crate) struct Sections<'a> {
    pub index: &'a [u8],
    pub summary: &'a [u8],
    pub identity: &'a [u8],
    pub history: &'a [u8],
}

/// Writes the tail and footer after the records that end at `records_end`.
pub(crate) fn write_tail(
    out: &mut impl Write,
    records_end: u64,
    sections: Sections<'_>,
) -> std::io::Result<()> {
    let Sections {
        index,
        summary,
        identity,
        history,
    } = sections;
    if index.len() as u64 > MAX_INDEX_LEN
        || summary.len() as u64 > MAX_SUMMARY_LEN
        || identity.len() as u64 > MAX_IDENTITY_LEN
        || history.len() as u64 > MAX_HISTORY_LEN
    {
        return Err(std::io::Error::other(
            "the file has more samples or metadata than the scene cache can hold",
        ));
    }
    let summary_at = records_end + index.len() as u64;
    let identity_at = summary_at + summary.len() as u64;
    let history_at = identity_at + identity.len() as u64;
    let mut checksum = crc32fast::Hasher::new();
    for section in [index, summary, identity, history] {
        checksum.update(section);
        out.write_all(section)?;
    }
    for word in [records_end, summary_at, identity_at, history_at] {
        out.write_all(&word.to_le_bytes())?;
    }
    out.write_all(&checksum.finalize().to_le_bytes())?;
    out.write_all(END)
}

/// Reads and checks the Scene record at `start..end`. The reason is why it cannot be read.
pub(crate) fn read_record(file: &File, start: u64, end: u64, crc: u32) -> Result<Scene, String> {
    // `Layout` checked that no record is longer than MAX_RECORD_LEN.
    let mut bytes = vec![0_u8; (end - start) as usize];
    read_exact_at(file, &mut bytes, start).map_err(|e| e.to_string())?;
    if crc32fast::hash(&bytes) != crc {
        return Err("checksum mismatch".to_string());
    }
    serial::decode(&bytes).map_err(|e| e.to_string())
}

/// Writes the file header.
pub(crate) fn write_header(out: &mut impl Write) -> std::io::Result<()> {
    out.write_all(MAGIC)?;
    out.write_all(&STORE_FORMAT_VERSION.to_le_bytes())
}
