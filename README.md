<p align="center">
  <img src="assets/ax-devil-banner.png" alt="ax-devil — Axis camera viewer with analytics overlays" width="100%" />
</p>

**ax-devil** is a desktop app for viewing live Axis camera streams and offline recordings together with analytics
overlays, inspecting objects frame by frame, and comparing results side by side.

> **Being rebuilt.** This branch is a from-scratch rewrite in Rust, React and Electron; it opens an
> empty window and has no features yet. What it will do and in which order is in the
> [rebuild playbook](docs/rebuild/README.md); progress is in [docs/parity.md](docs/parity.md). The Python app it replaces
> is readable at commit `e31a7453`.

## Build from source

Requirements: Linux, Make, Node 22.18+, 24.11+ or 26+, pnpm 12.14.0, curl, a C toolchain (`cc`), and Xvfb for the
desktop tests. Rust comes from [rustup](https://rustup.rs) if installed; otherwise `make` installs the pinned
toolchain inside the checkout on first use.

```bash
make desktop   # The app in an Electron window, with live reload
make web       # The same app at http://127.0.0.1:7331
make build     # Release build: target/release/ax-devil with the frontend embedded
make check     # Every check and test suite
```

`make help` lists every target; there is no separate setup step. Testing is described in
[docs/runbooks/testing.md](docs/runbooks/testing.md).

## Contributing

Start with [AGENTS.md](AGENTS.md), which coding agents and people follow alike, then the
[architecture overview](docs/architecture/overview.md) and [module map](docs/architecture/module-map.md).

## License

[MIT](LICENSE). Icons: [Lucide](assets/icons/LICENSE), ISC.
