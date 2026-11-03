# File Watching

`FileWatcher` tells its owner which registered files changed on disk. An owner registers a `SourceSnapshot`
(`ax_devil.core.source_paths`, taken with `SourcePaths.snapshot` before it reads the files) under a key and hears
`changed(key, paths)` with the registered paths that were written, replaced, added or removed.

- A file is watched with the folder holding it, a folder with every subfolder (symbolic links are not followed, and
  at most 4096 folders are watched per watcher). A path that does not exist yet is watched at its nearest existing
  parent and reported when it appears.
- A folder is watched for which files it holds: each walked folder is fingerprinted (its modification time changes when
  an entry is added, removed or renamed), the files in it are not, and a change is reported as the folder. A file whose
  contents matter must be named in `SourcePaths.files`; those get a watch and a fingerprint of their own.
- Checking (reading folders and fingerprinting: inode, size, modification time) runs on a background pool. The
  snapshot is the baseline, so a change made after it was taken is reported by the first check; each difference from the
  baseline is reported once, and the baseline then moves on. Watching a key again with different paths keeps the
  fingerprints it was last reported on for paths it already covered, so a fresh snapshot cannot hide a change; only
  paths new to the key start from the snapshot.
- Events only restart a one-second quiet timer. A file still changing between two reads is held back until it stops.
- A watch the system refuses (for example at an inotify limit) is retried, and its key checked, every two seconds
  while it stays refused, so a rewrite of such a file is still reported late.
- Only local disks are noticed: changes on network shares (NFS, SMB) produce no events.
