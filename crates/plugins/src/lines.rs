//! Splits a plugin's stdout into lines without ever holding more than one capped line.

use std::ops::ControlFlow;

use memchr::memchr;

/// A line that does not end within the cap.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct TooLong;

/// Assembles lines from the chunks a pipe delivers. A line over `max` bytes is refused before it is buffered, so
/// the buffer never holds more than `max`.
pub(crate) struct LineBuffer {
    pending: Vec<u8>,
    /// What followed the line at which a `push` stopped; the next `push` reads it first.
    held: Vec<u8>,
    max: usize,
    number: usize,
}

impl LineBuffer {
    pub(crate) fn new(max: usize) -> Self {
        Self {
            pending: Vec::new(),
            held: Vec::new(),
            max,
            number: 0,
        }
    }

    /// The number the next line gets, counting from 1.
    pub(crate) fn next_number(&self) -> usize {
        self.number + 1
    }

    /// Calls `line(number, bytes)` for each line `chunk` completes (without `\n`, with any `\r` still on), until it
    /// returns `Break`. The bytes after that line are kept and read first by the next call, which may pass an empty
    /// chunk.
    pub(crate) fn push<B>(
        &mut self,
        chunk: &[u8],
        mut line: impl FnMut(usize, &[u8]) -> ControlFlow<B>,
    ) -> Result<ControlFlow<B>, TooLong> {
        let joined;
        let mut chunk = if self.held.is_empty() {
            chunk
        } else {
            joined = [std::mem::take(&mut self.held).as_slice(), chunk].concat();
            joined.as_slice()
        };
        while let Some(end) = memchr(b'\n', chunk) {
            let (head, rest) = (&chunk[..end], &chunk[end + 1..]);
            self.number += 1;
            let flow = if self.pending.is_empty() {
                if head.len() > self.max {
                    return Err(TooLong);
                }
                line(self.number, head)
            } else {
                if self.pending.len() + head.len() > self.max {
                    return Err(TooLong);
                }
                self.pending.extend_from_slice(head);
                let flow = line(self.number, &self.pending);
                self.pending.clear();
                flow
            };
            if flow.is_break() {
                self.held = rest.to_vec();
                return Ok(flow);
            }
            chunk = rest;
        }
        if self.pending.len() + chunk.len() > self.max {
            return Err(TooLong);
        }
        self.pending.extend_from_slice(chunk);
        Ok(ControlFlow::Continue(()))
    }

    /// Hands over the last line when the stream ended without a `\n`.
    pub(crate) fn finish<B>(
        &mut self,
        mut line: impl FnMut(usize, &[u8]) -> ControlFlow<B>,
    ) -> ControlFlow<B> {
        if self.pending.is_empty() {
            return ControlFlow::Continue(());
        }
        self.number += 1;
        let flow = line(self.number, &self.pending);
        self.pending.clear();
        flow
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lines(chunks: &[&[u8]], max: usize) -> (Vec<(usize, String)>, Result<(), TooLong>) {
        let mut buffer = LineBuffer::new(max);
        let mut seen = Vec::new();
        let mut collect = |number: usize, bytes: &[u8]| -> ControlFlow<()> {
            seen.push((number, String::from_utf8_lossy(bytes).into_owned()));
            ControlFlow::Continue(())
        };
        for chunk in chunks {
            if buffer.push(chunk, &mut collect).is_err() {
                return (seen, Err(TooLong));
            }
        }
        let _ = buffer.finish(&mut collect);
        (seen, Ok(()))
    }

    #[test]
    fn lines_join_across_chunks_and_count_blank_ones() {
        let (seen, end) = lines(&[b"ab", b"c\n\nd", b"e\r\nlast"], 100);
        assert_eq!(end, Ok(()));
        assert_eq!(
            seen,
            [
                (1, "abc".to_owned()),
                (2, String::new()),
                (3, "de\r".to_owned()),
                (4, "last".to_owned())
            ]
        );
    }

    #[test]
    fn lines_behind_a_stop_are_kept_for_the_next_push() {
        let mut buffer = LineBuffer::new(100);
        let mut seen = Vec::new();
        let mut stop_at_a = |_, bytes: &[u8]| {
            seen.push(String::from_utf8_lossy(bytes).into_owned());
            if bytes == b"a" {
                ControlFlow::Break(())
            } else {
                ControlFlow::Continue(())
            }
        };
        assert_eq!(
            buffer.push(b"a\nb\nc", &mut stop_at_a),
            Ok(ControlFlow::Break(()))
        );
        // An empty push reads what the stop left: "b" completes, "c" waits for its newline.
        assert_eq!(
            buffer.push(b"", &mut stop_at_a),
            Ok(ControlFlow::Continue(()))
        );
        assert_eq!(
            buffer.push(b"d\n", &mut stop_at_a),
            Ok(ControlFlow::Continue(()))
        );
        assert_eq!(seen, ["a", "b", "cd"]);
    }

    #[test]
    fn a_line_over_the_cap_is_refused_even_when_split_across_chunks() {
        let (seen, end) = lines(&[b"1234\n12", b"345", b"6\n"], 5);
        assert_eq!(end, Err(TooLong));
        assert_eq!(seen, [(1, "1234".to_owned())]);

        let (seen, end) = lines(&[b"12345\n"], 5);
        assert_eq!((seen.len(), end), (1, Ok(())));
    }
}
