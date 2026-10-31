# What to Carry Over

What exists at the reference commit and must be brought into the new tree by hand. Carry = copy unchanged; Port =
recreate for the new stack using the old as reference; Drop = do not bring over. Nothing is converted automatically.

| Artifact | At reference commit | Action | Reason |
|----------|---------------------|--------|--------|
| Built-in render catalogs | `standard.json`, `minimal.json`, `chunky.json`, `glass.json`, `tracking.json`, `classic.json` in `scene/rendering/catalog_definitions/` | Carried to new-tree `assets/catalogs/` | Catalog language is kept ([05](05-formats-and-extensions.md#format-policy)) |
| Catalog JSON Schema and language reference | `catalog.schema.json` in the same folder; `ax-devil catalog reference` | Port | Generated from the new runtime definitions; diff against the old as a check |
| Icons and their license | `src/ax_devil/resources/icons/` | Carried to new-tree `assets/icons/` | Same icon set (Lucide, ISC) |
| Banner image | `src/ax_devil/resources/ax-devil-banner.png` | Carried to new-tree `assets/` | README image |
| Default settings values | `DEFAULT_CONFIG` in `settings/config_manager.py` | Port | Values for the settings model ([08](08-settings-cli.md#settings-model)) |
| Default shortcuts | `DEFAULT_SHORTCUTS` in `shortcuts/shortcuts.py` | Port | Same table as data |
| Workspace file format | `docs/architecture/workspace.md#file-format` | Port | Design reference; the new app may start its own version |
| Config format | `settings/config_manager.py` | Port | Fresh format |
| Built-in decoders (ADF beta and v1, CVAT, MOT, ONVIF XML, UVG-VCM) and resolvers (folder_pair, mot_challenge) | `src/ax_devil/plugins/` | Port | Native in the new language (D1) |
| Write-plugin example package | `.agents/skills/write-plugin/reference.md` | Port | Contract test for the plugin runtime |
| ONVIF sample payload | `tests/plugins/decoders/onvif_xml/data/sample_frame.xml` | Carried to new-tree `fixtures/decoders/onvif_xml/` | The only committed decoder sample file |
| Other decoder sample inputs | inline in tests, e.g. `tests/plugins/decoders/cvat/sample_data.py` (CVAT, MOT, ADF, UVG-VCM) | Generate | Extract into small sample files |
| Oracle fixtures | generated in a reference worktree | Generate | [09](09-testing.md#oracle-policy), [14](14-build-order.md#fixtures-from-the-reference-commit) |
| Glossary, invariants | `CONTEXT.md`, `docs/domain/invariants.md` | Ported by the foundation | [04](04-domain-rules.md) |
| Other docs, agent files, skills | see [11](11-docs-structure.md#docs-map), [12](12-agent-setup.md#inventory) | Per those tables | — |
| Dataset docs | `docs/datasets/` | Port | Commands change |
| `LICENSE` | root | Carried (kept) | Unchanged |
| `CHANGELOG.md` | root | Ported by the foundation | Fresh Unreleased entry |
| Task runner, CI, pre-commit, check scripts | `Makefile`, `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `scripts/` | Ported by the foundation ([14](14-build-order.md#decisions-taken-for-the-foundation)) | Gates of [10](10-quality-gates.md) |
| Developer tools | `tools/` | Port per DT row; drop `tools/show_timestamps.py` | DT-04 covers alignment |
| Shaders, QML, packaging metadata, lockfile | `video_player/engine/quick/`, `pyproject.toml`, `uv.lock` | Drop | Stack-specific |
| Demo media | `docs/media/` | Drop | Re-record with DT-06 |
| Users' configs, workspace files, caches, recents, recovery copies, plugin environments | `~/.ax_devil/`, XDG data folder | Drop | The new app does not read old data |
| Third-party Python plugins | users' own packages | Port by their authors | Default of D1; PL-05 not planned |
