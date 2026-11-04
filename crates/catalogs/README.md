# catalogs

The render catalogs a lane may draw with: the built-in documents and the `*.json` files of the user's catalog folder
(`storage.catalog_dir`, else `$XDG_DATA_HOME/ax-devil/catalogs`). This crate lists and serves text; the frontend's
compiler is the only judge of the language, so a file that is not a catalog is still listed.

- The folder is polled by `Library::rescan`, at most 256 files of 1 MiB. Only regular files are read: a link, pipe or
  device is listed with a problem, never followed, and a file is judged on the handle it was opened with (no-follow,
  non-blocking). `Library::copy` and `Library::delete` are the only writes: a copy takes a new `<slug>.json` (`-2`,
  `-3`, ... on a clash) and never overwrites, and both list their result at once.
- Listing order: the built-ins, then readable user files by name ignoring case (file name breaks ties), then invalid
  ones (`CatalogInfo::problem` set) by file name.
- A file is read only after it looked the same on two polls in a row, and counts as deleted only after it was missing
  on two: an editor's half-written or swapped save never reaches a lane.
- Revisions come from one counter for the process (built-ins are 1), so a replaced or re-created file never gets a
  revision a viewer holds. A restart restarts the counter; viewers take any revision that differs from theirs.
- `sheets` builds the example Scene (the Overview) that `catalog render` draws a catalog on: a grey picture and a Sample Item
  from the real item encoder, so a catalog is shown what the viewer would show it.
