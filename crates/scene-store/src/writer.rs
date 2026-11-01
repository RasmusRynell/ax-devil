//! Writing an artifact: Scenes are encoded as they are emitted and dropped, so memory holds one record and the keys.

use std::io::Write;

use scene::{Scene, SceneTime, TimeKind, serial};
use scene_decoders::LoadWarning;

use crate::{
    format::{HEADER_LEN, Identity, Index, Keys, Layout, MAX_RECORD_LEN, write_header, write_tail},
    summary::Summary,
};

/// What the writer returns when the file is complete.
pub(crate) struct Finished<W> {
    pub out: W,
    pub layout: Layout,
    pub summary: Summary,
}

/// Appends Scene records to an artifact being built.
pub(crate) struct RecordWriter<W: Write> {
    out: W,
    position: u64,
    boundaries: Vec<u64>,
    crcs: Vec<u32>,
    keys: Option<Keys>,
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
            keys: None,
            scratch: Vec::new(),
        })
    }

    /// Appends one Scene. The reason is why the file cannot become an artifact.
    pub(crate) fn add(&mut self, scene: &Scene) -> Result<(), String> {
        let start = scene.time().start();
        let keys = self.keys.get_or_insert_with(|| match start.kind() {
            TimeKind::Frame => Keys::Frames(Vec::new()),
            TimeKind::Timestamp => Keys::Times(Vec::new()),
        });
        match (keys, start) {
            (Keys::Frames(keys), SceneTime::Frame(frame)) => keys.push(frame),
            (Keys::Times(keys), SceneTime::Timestamp(time)) => keys.push(time.0),
            _ => {
                return Err(
                    "the file mixes frame numbers and timestamps; a file must use one".into(),
                );
            }
        }
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
        Ok(())
    }

    /// Orders the samples by key, writes the tail and flushes.
    pub(crate) fn finish(
        mut self,
        mut summary: Summary,
        identity: &Identity,
    ) -> Result<Finished<W>, String> {
        let keys = self.keys.take().unwrap_or(Keys::Frames(Vec::new()));
        let (keys, order, repeated) = by_key(keys)?;
        if repeated > 0 {
            summary.warn(&LoadWarning {
                problem: format!("{repeated} samples share their key with a later one"),
                reasons: Vec::new(),
                consequence: "Only the last sample of each key is shown.".to_string(),
                more_reasons: false,
            });
        }
        let records_end = self.position;
        let index = Index {
            keys,
            boundaries: self.boundaries,
            crcs: self.crcs,
            order,
        };
        let index_bytes = postcard::to_stdvec(&index).map_err(|e| e.to_string())?;
        let summary_bytes = postcard::to_stdvec(&summary).map_err(|e| e.to_string())?;
        write_tail(
            &mut self.out,
            records_end,
            &index_bytes,
            &summary_bytes,
            identity,
        )
        .and_then(|()| self.out.flush())
        .map_err(|e| format!("cannot write the scene cache: {e}"))?;
        Ok(Finished {
            layout: index.into_layout(records_end)?,
            out: self.out,
            summary,
        })
    }
}

/// Sorts the keys, keeps the last record of each repeated key, and returns the record of each sample (empty when the
/// records already were in key order) and how many records were dropped.
fn by_key(keys: Keys) -> Result<(Keys, Vec<u32>, usize), String> {
    fn arrange<K: Ord + Copy>(keys: Vec<K>) -> Result<(Vec<K>, Vec<u32>, usize), String> {
        if keys.windows(2).all(|pair| pair[0] < pair[1]) {
            return Ok((keys, Vec::new(), 0));
        }
        u32::try_from(keys.len()).map_err(|_| "the file has too many samples".to_string())?;
        let mut records: Vec<u32> = (0..keys.len() as u32).collect();
        records.sort_by_key(|&record| keys[record as usize]);
        let mut sorted = Vec::with_capacity(keys.len());
        let mut order: Vec<u32> = Vec::with_capacity(keys.len());
        for record in records {
            let key = keys[record as usize];
            if sorted.last() == Some(&key) {
                // The sort is stable, so the later record of a repeated key comes last and replaces the earlier.
                order.pop();
                sorted.pop();
            }
            sorted.push(key);
            order.push(record);
        }
        let repeated = keys.len() - sorted.len();
        Ok((sorted, order, repeated))
    }
    Ok(match keys {
        Keys::Frames(keys) => {
            let (keys, order, repeated) = arrange(keys)?;
            (Keys::Frames(keys), order, repeated)
        }
        Keys::Times(keys) => {
            let (keys, order, repeated) = arrange(keys)?;
            (Keys::Times(keys), order, repeated)
        }
    })
}
