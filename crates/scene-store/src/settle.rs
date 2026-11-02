//! Putting fragments that arrived out of order into key order: the second pass over the artifact.

use std::{fs::File, io::BufWriter};

use foundation::{CancelToken, Interrupt, MediaTime};
use scene::{Scene, SceneTime, TimeKind};

use crate::{
    format::{Keys, read_record},
    group::Group,
    history::HistoryBuilder,
    ingest::GROUP_BYTES,
    keylog::KeyLog,
    sort::{Entry, Sorted, sort_key, time_of},
    writer::RecordWriter,
};

/// Everything the pass reads: the first artifact with its records, the keys of the records that were already in
/// order, and the fragments sorted by key.
pub(crate) struct Fragments<'a> {
    pub first: &'a RecordWriter<BufWriter<File>>,
    pub reader: &'a File,
    pub kind: TimeKind,
    /// The keys of records `0..n`, which were in order and each hold a whole group.
    pub ordered: Keys,
    pub fragments: Sorted,
}

/// Writes one Scene per key to `out`, in key order, joining the records of equal keys as [`Group`] does, and
/// returns how many records replaced earlier ones. Each written Scene is added to `history`. Records of one key are joined in the order they were written.
pub(crate) fn merge_fragments(
    input: Fragments<'_>,
    out: &mut RecordWriter<BufWriter<File>>,
    keys: &mut KeyLog,
    history: &mut HistoryBuilder,
    cancel: &CancelToken,
) -> Result<usize, Interrupt<String>> {
    let Fragments {
        first,
        reader,
        kind,
        ordered,
        fragments,
    } = input;
    let read = |entry: Entry| -> Result<Scene, String> {
        let (start, end, crc) = first
            .span(entry.record)
            .ok_or_else(|| "a record is missing from the scene cache".to_string())?;
        read_record(reader, start, end, crc)
    };
    let mut entries = Merged::new(ordered, fragments).map_err(failed)?;
    let mut replaced = 0;
    let mut next = entries.next().map_err(failed)?;
    while let Some(head) = next {
        cancel.check()?;
        let mut group = Group::new(read(head).map_err(failed)?);
        next = loop {
            let Some(entry) = entries.next().map_err(failed)? else {
                break None;
            };
            if entry.key != head.key {
                break Some(entry);
            }
            let scene = read(entry).map_err(failed)?;
            if entry.replace {
                replaced += 1;
                group = Group::new(scene);
            } else {
                group.merge(&scene).map_err(failed)?;
            }
            if group.bytes() > GROUP_BYTES {
                return Err(failed("a sample is too large to be joined"));
            }
        };
        let scene = group.into_scene().map_err(failed)?;
        out.add(&scene).map_err(failed)?;
        history.add(&scene);
        keys.push(time_of(kind, head.key));
    }
    Ok(replaced)
}

fn failed(reason: impl ToString) -> Interrupt<String> {
    Interrupt::Failed(reason.to_string())
}

/// The in-order records and the sorted fragments as one stream in `(key, record)` order.
struct Merged {
    ordered: std::vec::IntoIter<u64>,
    at: u32,
    fragments: Sorted,
    head_ordered: Option<Entry>,
    head_fragment: Option<Entry>,
}

impl Merged {
    fn new(ordered: Keys, fragments: Sorted) -> Result<Self, String> {
        let keys: Vec<u64> = match ordered {
            Keys::Frames(keys) => keys
                .into_iter()
                .map(|frame| sort_key(SceneTime::Frame(frame)))
                .collect(),
            Keys::Times(keys) => keys
                .into_iter()
                .map(|time| sort_key(SceneTime::Timestamp(MediaTime(time))))
                .collect(),
        };
        let mut merged = Self {
            ordered: keys.into_iter(),
            at: 0,
            fragments,
            head_ordered: None,
            head_fragment: None,
        };
        merged.head_ordered = merged.next_ordered();
        merged.head_fragment = merged.next_fragment()?;
        Ok(merged)
    }

    fn next_ordered(&mut self) -> Option<Entry> {
        let key = self.ordered.next()?;
        let record = self.at;
        self.at += 1;
        Some(Entry {
            key,
            record,
            replace: false,
        })
    }

    fn next_fragment(&mut self) -> Result<Option<Entry>, String> {
        self.fragments
            .next()
            .transpose()
            .map_err(|e| format!("cannot read the scene cache: {e}"))
    }

    fn next(&mut self) -> Result<Option<Entry>, String> {
        Ok(match (self.head_ordered, self.head_fragment) {
            (Some(ordered), Some(fragment)) if ordered <= fragment => {
                self.head_ordered = self.next_ordered();
                Some(ordered)
            }
            (_, Some(fragment)) => {
                self.head_fragment = self.next_fragment()?;
                Some(fragment)
            }
            (Some(ordered), None) => {
                self.head_ordered = self.next_ordered();
                Some(ordered)
            }
            (None, None) => None,
        })
    }
}
