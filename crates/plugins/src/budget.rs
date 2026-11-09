//! How much free-form metadata a resolver may state: it is shown in Information, so it is kept small.

use std::io;

use handler_api::Settings;

/// The most JSON bytes one `metadata` object may take (16 KiB).
pub(crate) const OBJECT_BYTES: usize = 16 * 1024;
/// The most JSON bytes all metadata of one resolve may take together (2 MiB).
pub(crate) const RUN_BYTES: usize = 2 * 1024 * 1024;

/// The metadata taken so far by one resolve.
#[derive(Default)]
pub(crate) struct Budget {
    used: usize,
}

impl Budget {
    /// Takes `metadata`, or fails with what the plugin must change: an object over [`OBJECT_BYTES`], or one that
    /// would take the run past [`RUN_BYTES`]. A refused object takes nothing.
    pub(crate) fn take(&mut self, metadata: &Settings) -> Result<(), String> {
        if metadata.is_empty() {
            return Ok(());
        }
        let size = json_len(metadata);
        if size > OBJECT_BYTES {
            return Err(format!(
                "a metadata object is {size} bytes; at most {OBJECT_BYTES} are allowed"
            ));
        }
        if self.used + size > RUN_BYTES {
            return Err(format!(
                "the metadata of all playlists, entries and overlays is more than {RUN_BYTES} bytes"
            ));
        }
        self.used += size;
        Ok(())
    }
}

/// The length of `metadata` as compact JSON, counted without building it.
fn json_len(metadata: &Settings) -> usize {
    struct Count(usize);
    impl io::Write for Count {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.0 += bytes.len();
            Ok(bytes.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    let mut count = Count(0);
    // Counting cannot fail, and a map of JSON values always serializes.
    let _ = serde_json::to_writer(&mut count, metadata);
    count.0
}
