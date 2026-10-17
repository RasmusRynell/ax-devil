# File Watching

`FileWatcher` tells its owner which registered files changed on disk. An owner registers `SourcePaths`
(`ax_devil.core.source_paths`) under a key and hears `changed(key, paths)` with the registered paths that were written,
replaced, added or removed.

- A file is watched with the folder holding it, a folder with every subfolder (symbolic links are not followed, and
  at most 4096 folders are watched per watcher). A path that does not exist yet is watched at its nearest existing
  parent and reported when it appears.
- A folder is watched for which files it holds: each walked folder is fingerprinted (its modification time changes when
  an entry is added, removed or renamed), the files in it are not, and a change is reported as the folder. A file whose
  contents matter must be named in `SourcePaths.files`; those get a watch and a fingerprint of their own.
- Reading folders and fingerprinting (inode, size, modification time) runs on a background pool. What exists when
  `watch` is called is the baseline; only differences from it are reported, once, and the baseline then moves on.
  Watching a key again with different paths keeps its baseline: changes to paths it already covered are still reported,
  and paths it did not cover are taken into the baseline by the next check without being reported.
- Events only restart a one-second quiet timer. A file still changing between two reads is held back until it stops.
- Only local disks are noticed: changes on network shares (NFS, SMB) produce no events.
