# settings

The settings service (layer `services`): loads the config file, validates changes, saves them atomically, and
publishes one immutable `State`. Domain code takes settings as values; only the bootstrap wires the service in.
`TABLE` is the one declaration of every setting; shortcuts are declared in `shortcuts.rs`.

## Scopes and defaults

Each row in `TABLE` has a `Scope`: Personal (how one person's app behaves: theme, shortcuts) or Server (what the
server works with: the video cache, the cache folder). The scope groups rows for the UI; a caller reads and changes
every row. A literal in a `secret` row is shown masked and the masked value sent back keeps the stored one.

Settings supply defaults at the moment something is created, and the value is copied in: a workspace, video or saved
layout never refers back to the settings file, and nothing shareable embeds a setting, a `$NAME` reference or a
credential. Only shortcuts are read live.

## File

```json
{ "version": 1, "settings": { "ui.theme": "dark" }, "shortcuts": { "save-workspace": "Ctrl+Alt+S", "exit": null } }
```

- Flat dotted keys. The file is sparse: only values that differ from the default are written, reset deletes the key,
  and a missing key is the default. A shortcut of `null` is unbound.
- A row with `env` accepts a whole-value `$NAME` reference, stored as written and expanded where it is used
  (`State::resolved`), silently. A number or folder is checked as expanded on save; text and a choice are read only
  when used. A reference that gives nothing usable is logged without its value and gives empty text, or the default
  where empty is not valid. A folder is read once at start; `State::references` gives a form the values.
- Unknown top-level keys, settings keys and command ids are logged and kept on save, so another version's settings
  survive. A known key with an invalid value, malformed JSON, a missing or non-integer `version`, or a file newer
  than `VERSION` stops loading with an error naming the file and key.
- Window state is a separate, lenient file (`window.rs`): a bad file gives no saved windows and a warning. It belongs
  to this machine and is served only on the Local listener.

## Changing the format

Bump the version only when an old file would no longer mean the same thing (a renamed or re-typed key, a changed
value). Adding a row needs no migration: a missing key is the default.

1. Copy the released file shape to `fixtures/settings/v<N>.json` (N = the version being left) with entries that
   exercise it.
2. Append a function to `MIGRATIONS` in `migrate.rs`. It upgrades the whole document except `version` by one version;
   `VERSION` follows from the list length. Never edit a released step.

An older file is copied byte for byte to `<name>.v<N>.json` before it is migrated, validated and written at once. A
backup is never overwritten: an identical one is reused, a different one makes the copy `<name>.v<N>.<n>.json`. Saving
replaces a symlinked `settings.json` with a private (0600) file and never writes through the link.
`every_released_settings_file_still_loads` runs every fixture through the real chain.
