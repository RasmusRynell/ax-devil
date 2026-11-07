//! Ordering the fragments of a file whose keys do not ascend, in memory up to a budget and on disk beyond it.

use std::{
    cmp::Reverse,
    collections::BinaryHeap,
    fs::File,
    io::{self, Write},
    path::PathBuf,
    vec,
};

use scene::{SceneTime, TimeKind};

use crate::io::read_exact_at;

/// How many bytes of entries the sorter holds before it writes a sorted run to disk.
pub(crate) const SORT_BUDGET_BYTES: usize = 64 << 20;

const ENTRY_BYTES: usize = 24;
/// How many entries a run reader holds.
const READ_ENTRIES: usize = 4096;

/// One fragment record: the sample key it belongs to and where it is. The payload stays in the artifact.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct Entry {
    /// [`sort_key`] of the start time.
    pub key: u64,
    /// The record in the artifact being sorted; also the order the fragments came in.
    pub record: u32,
    /// Whether the fragment replaces what its key held before it.
    pub replace: bool,
    /// The position in the file of the record the fragment came from.
    pub ordinal: u64,
}

impl Entry {
    fn to_bytes(self) -> [u8; ENTRY_BYTES] {
        let mut bytes = [0; ENTRY_BYTES];
        bytes[..8].copy_from_slice(&self.key.to_le_bytes());
        bytes[8..12].copy_from_slice(&self.record.to_le_bytes());
        bytes[12..16].copy_from_slice(&u32::from(self.replace).to_le_bytes());
        bytes[16..].copy_from_slice(&self.ordinal.to_le_bytes());
        bytes
    }

    fn from_bytes(bytes: &[u8; ENTRY_BYTES]) -> Self {
        let word = |at: usize| {
            let mut four = [0; 4];
            four.copy_from_slice(&bytes[at..at + 4]);
            u32::from_le_bytes(four)
        };
        let long = |at: usize| {
            let mut eight = [0; 8];
            eight.copy_from_slice(&bytes[at..at + 8]);
            u64::from_le_bytes(eight)
        };
        Self {
            key: long(0),
            record: word(8),
            replace: word(12) != 0,
            ordinal: long(16),
        }
    }
}

/// A number that orders like the time, whichever clock it is on.
pub(crate) fn sort_key(time: SceneTime) -> u64 {
    match time {
        SceneTime::Frame(frame) => frame,
        SceneTime::Timestamp(time) => (time.0 as u64) ^ (1 << 63),
    }
}

/// The time of a [`sort_key`] on `kind`'s clock.
pub(crate) fn time_of(kind: TimeKind, key: u64) -> SceneTime {
    match kind {
        TimeKind::Frame => SceneTime::Frame(key),
        TimeKind::Timestamp => {
            SceneTime::Timestamp(foundation::MediaTime((key ^ (1 << 63)) as i64))
        }
    }
}

/// Collects entries and hands them back in `(key, record)` order, using memory up to a budget and one temporary
/// file beyond it.
pub(crate) struct Sorter {
    capacity: usize,
    buffer: Vec<Entry>,
    /// Where spilled runs go: a temporary file in this folder, or the system's when there is none.
    folder: Option<PathBuf>,
    spill: Option<Spill>,
}

struct Spill {
    file: File,
    /// Offset and entry count of each sorted run.
    runs: Vec<(u64, usize)>,
    end: u64,
}

impl Sorter {
    pub(crate) fn new(budget_bytes: usize, folder: Option<PathBuf>) -> Self {
        Self {
            capacity: (budget_bytes / ENTRY_BYTES).max(2),
            buffer: Vec::new(),
            folder,
            spill: None,
        }
    }

    pub(crate) fn push(&mut self, entry: Entry) -> io::Result<()> {
        self.buffer.push(entry);
        if self.buffer.len() >= self.capacity {
            self.spill_buffer()?;
        }
        Ok(())
    }

    fn spill_buffer(&mut self) -> io::Result<()> {
        self.buffer.sort_unstable();
        let spill = match &mut self.spill {
            Some(spill) => spill,
            None => self.spill.insert(Spill {
                file: match &self.folder {
                    Some(folder) => tempfile::tempfile_in(folder)?,
                    None => tempfile::tempfile()?,
                },
                runs: Vec::new(),
                end: 0,
            }),
        };
        let bytes: Vec<u8> = self
            .buffer
            .iter()
            .flat_map(|entry| entry.to_bytes())
            .collect();
        spill.file.write_all(&bytes)?;
        spill.runs.push((spill.end, self.buffer.len()));
        spill.end += bytes.len() as u64;
        self.buffer.clear();
        Ok(())
    }

    /// Everything pushed, in order.
    pub(crate) fn finish(mut self) -> io::Result<Sorted> {
        if self.spill.is_none() {
            self.buffer.sort_unstable();
            return Ok(Sorted::Memory(self.buffer.into_iter()));
        }
        if !self.buffer.is_empty() {
            self.spill_buffer()?;
        }
        let Some(spill) = self.spill else {
            return Ok(Sorted::Memory(Vec::new().into_iter()));
        };
        let mut readers: Vec<Run> = spill
            .runs
            .iter()
            .map(|&(offset, count)| Run {
                offset,
                remaining: count,
                buffer: Vec::new(),
                next: 0,
            })
            .collect();
        let mut heap = BinaryHeap::new();
        for (run, reader) in readers.iter_mut().enumerate() {
            if let Some(entry) = reader.next(&spill.file)? {
                heap.push(Reverse((entry, run)));
            }
        }
        Ok(Sorted::Merge(Merge {
            file: spill.file,
            readers,
            heap,
            failed: false,
        }))
    }
}

/// Entries in order; an item is an error when the temporary file cannot be read.
pub(crate) enum Sorted {
    Memory(vec::IntoIter<Entry>),
    Merge(Merge),
}

pub(crate) struct Merge {
    file: File,
    readers: Vec<Run>,
    heap: BinaryHeap<Reverse<(Entry, usize)>>,
    failed: bool,
}

/// A sorted run on disk, read a few thousand entries at a time.
struct Run {
    offset: u64,
    remaining: usize,
    buffer: Vec<Entry>,
    next: usize,
}

impl Run {
    fn next(&mut self, file: &File) -> io::Result<Option<Entry>> {
        if self.next == self.buffer.len() {
            if self.remaining == 0 {
                return Ok(None);
            }
            let take = self.remaining.min(READ_ENTRIES);
            let mut bytes = vec![0_u8; take * ENTRY_BYTES];
            read_exact_at(file, &mut bytes, self.offset)?;
            self.offset += bytes.len() as u64;
            self.remaining -= take;
            self.buffer = bytes
                .as_chunks::<ENTRY_BYTES>()
                .0
                .iter()
                .map(Entry::from_bytes)
                .collect();
            self.next = 0;
        }
        let entry = self.buffer[self.next];
        self.next += 1;
        Ok(Some(entry))
    }
}

impl Iterator for Sorted {
    type Item = io::Result<Entry>;

    fn next(&mut self) -> Option<Self::Item> {
        match self {
            Self::Memory(entries) => entries.next().map(Ok),
            Self::Merge(merge) => {
                if merge.failed {
                    return None;
                }
                let Reverse((entry, run)) = merge.heap.pop()?;
                match merge.readers[run].next(&merge.file) {
                    Ok(Some(next)) => merge.heap.push(Reverse((next, run))),
                    Ok(None) => {}
                    Err(error) => {
                        merge.failed = true;
                        return Some(Err(error));
                    }
                }
                Some(Ok(entry))
            }
        }
    }
}
