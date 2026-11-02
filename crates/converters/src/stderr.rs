//! What a converter wrote to stderr: drained so it never blocks, kept only as a short tail.

use std::{
    collections::VecDeque,
    io::Read,
    process::ChildStderr,
    sync::{Arc, Mutex},
};

/// Most stderr bytes kept.
const KEEP_BYTES: usize = 64 << 10;
/// Most lines, and bytes, of the tail a failure reason quotes.
const QUOTE_LINES: usize = 20;
const QUOTE_BYTES: usize = 2 << 10;

/// The last [`KEEP_BYTES`] of a converter's stderr.
#[derive(Clone, Default)]
pub(crate) struct StderrTail(Arc<Mutex<VecDeque<u8>>>);

impl StderrTail {
    fn push(&self, chunk: &[u8]) {
        let mut kept = self
            .0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        kept.extend(chunk.iter().skip(chunk.len().saturating_sub(KEEP_BYTES)));
        let excess = kept.len().saturating_sub(KEEP_BYTES);
        kept.drain(..excess);
    }

    /// Everything kept, as text.
    #[cfg(test)]
    pub(crate) fn kept(&self) -> Vec<u8> {
        let kept = self
            .0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        kept.iter().copied().collect()
    }

    /// The last 20 lines, at most 2 KiB, for a failure reason; empty when the converter wrote nothing.
    pub(crate) fn quote(&self) -> String {
        let bytes: Vec<u8> = {
            let kept = self
                .0
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            kept.iter().copied().collect()
        };
        let text = String::from_utf8_lossy(&bytes);
        let lines: Vec<&str> = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .collect();
        let tail = lines[lines.len().saturating_sub(QUOTE_LINES)..].join("\n");
        let mut start = tail.len().saturating_sub(QUOTE_BYTES);
        while !tail.is_char_boundary(start) {
            start += 1;
        }
        tail[start..].to_owned()
    }

    /// Reads `stderr` to its end on a thread of its own, logging it at DEBUG (it may echo the user's data, so never
    /// higher), and sends on `done` at the end.
    pub(crate) fn drain(
        &self,
        mut stderr: ChildStderr,
        converter: String,
        done: std::sync::mpsc::Sender<()>,
    ) {
        let tail = self.clone();
        let spawned = std::thread::Builder::new()
            .name(format!("converter-stderr-{converter}"))
            .spawn(move || {
                let mut chunk = vec![0_u8; 16 << 10];
                while let Ok(read) = stderr.read(&mut chunk) {
                    if read == 0 {
                        break;
                    }
                    tracing::debug!(
                        converter,
                        "stderr: {}",
                        String::from_utf8_lossy(&chunk[..read])
                    );
                    tail.push(&chunk[..read]);
                }
                let _ = done.send(());
            });
        if let Err(error) = spawned {
            tracing::warn!("cannot start the stderr reader: {error}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_flood_keeps_only_the_last_64_kib() {
        let tail = StderrTail::default();
        let line = format!("{}\n", "x".repeat(99));
        for _ in 0..100_000 {
            tail.push(line.as_bytes());
        }
        tail.push(b"the end\n");

        let kept = tail.kept();
        assert_eq!(kept.len(), 64 << 10);
        assert!(kept.ends_with(b"the end\n"));
    }

    #[test]
    fn a_reason_quotes_the_last_lines_within_two_kib() {
        let tail = StderrTail::default();
        for number in 0..50 {
            tail.push(format!("line {number}\n").as_bytes());
        }
        let quote = tail.quote();
        assert_eq!(quote.lines().count(), QUOTE_LINES);
        assert!(quote.ends_with("line 49"));

        tail.push("é".repeat(4_000).as_bytes());
        assert!(tail.quote().len() <= QUOTE_BYTES);
    }
}
