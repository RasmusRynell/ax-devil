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
- A viewer's `Priority` orders decode spans only within one source: a group several viewers need is as urgent as its
  most urgent viewer. Sources have their own workers and an equal cache share, so it never arbitrates between videos.
- `Authority` bounds what a stream's holder may ask: a follower's playhead and everything it keeps around it stay in a
  window worked out from the presenter's intent and constants alone, never from the holder's own `ahead` and `behind`.
  The route applies `clamp_demand` to each want, again whenever the authority or the presenter's clock moves, and to
  each frame before it is sent; the route owns the socket, so `playback` knows no sessions.
