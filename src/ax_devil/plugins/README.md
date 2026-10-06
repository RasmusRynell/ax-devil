# Built-in plugin bundles

This directory contains packaged decoder and playlist-resolver bundles. Each built-in bundle exposes
`plugin.py:PLUGIN_CLASS`; the loader imports them before external distribution entry points.

See the [write-plugin skill](../../../.agents/skills/write-plugin/SKILL.md) for writing external plugins and their
contracts, and [Installing and managing plugins](../../../docs/plugins.md) for installation and upgrades.
