# playback

What to decode and keep around each viewer's playhead, and its delivery. The crate doc in `src/lib.rs` says how the
parts fit; this file keeps the decisions the code leaves unexplained.

- Two decode workers per source (D9): the second takes the next keyframe group while the first reads on, and in
  backward mode decodes the previous group before the playhead reaches it.
- A decoder reads on rather than seeks where it can (`reads_on`), because a seek flushes FFmpeg's frame-thread
  pipeline.
- Eviction is farthest from the playhead, not least recently used: after a jump, the frames around the old position are
  the least likely to be wanted.
- Backward windows start on keyframes, and in a group longer than the budget move in half-budget blocks (`window.rs`):
  a group is never decoded again just to keep one more frame.
