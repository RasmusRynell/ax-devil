# workspace-files

How a workspace lives on disk. `workspace` is the domain model; this crate owns the file format, the atomic write,
recovery copies with their locks, and the recent workspaces list.

- **Format (`version: 2`).** Private DTOs in `document.rs`; the domain types carry no serde. Anything but integer
  `version` 2, invalid JSON, duplicate ids or a bookmark or reviewed mark naming no item fails the whole load. An item
  the build cannot read (unknown kind, malformed, unknown key) loads as `Source::Unreadable` and is written back as it
  was read. Item and bookmark DTOs deny unknown keys, so a newer file's extra data is kept, not dropped.
- **Paths.** Only `video` and overlay `path` are rebased: a path inside the file's folder is written relative with
  `/`, any other absolute, both folded lexically (`.`, `..`) without touching the disk. Playlist settings, overlay
  options and unreadable items are opaque. A symlinked workspace file is read and written at its target and anchors to
  the target's folder; the link stays.
- **Saving.** Temporary file in the target's folder, `sync_all`, rename. A failure keeps the old bytes and mode and
  leaves no temporary file. A new file is 0600; an existing file keeps its mode.
- **Recovery copies.** Named `<name><SUFFIX>`, then `<name> 2<SUFFIX>`, in one folder. Each is locked by the OS lock
  on a sidecar `<copy>.lock` (the atomic replace changes the copy's inode, so it cannot hold the lock). The OS drops
  the lock when the process dies, so there are no stale locks. A copy also records the workspace file it recovers.
- **Recents.** A lenient, unversioned JSON list, at most 8, newest first, deduplicated by lexical absolute path (a
  symlink is its own entry). Listing never touches the files named. Every change holds a lock on `<file>.lock`, so
  concurrent windows and backends lose nothing.
