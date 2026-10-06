# Built-in plugin bundles

This directory contains packaged decoder and playlist-resolver bundles. Each built-in bundle exposes
`plugin.py:PLUGIN_CLASS`; the loader imports them before external distribution entry points.

See [Creating and using plugins](../../../docs/plugins.md) for the complete external plugin example,
dependency installation, contracts, and migration instructions.
