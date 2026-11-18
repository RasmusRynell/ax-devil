# Changelog

All notable changes to ax-devil are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- ax-devil is being rewritten in Rust, React and Electron. The Python implementation is removed from this branch;
  the new app reads no data written by the old one (configs, workspaces, catalogs in the user folder, caches,
  plugins). Built-in render catalogs keep their format.
