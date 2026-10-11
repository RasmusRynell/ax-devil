# Plugin manager

This package owns the Plugins view of the sidebar: which plugins loaded, which failed and why, and installing,
updating, removing and reloading plugins from the running app.

- `catalog.py` (Qt-free) turns the registry's records and the installation's plugin selection into the view's rows.
  Packages go by their normalized distribution name; a package with any failed plugin is listed only as failed. Each
  built-in plugin is a row of its own. A row lists the decoders (by handler type, so a file and a live handler of the
  same type are one decoder that reads both) and playlist resolvers it gives; a row with a single one is that one.
  Only packages in the selection can be updated or removed.
- `plugin_changes.py` runs one change at a time on a worker thread through the launcher's `plugins` command, so
  plugins are managed by the base app even when this process runs in the plugin environment. A change applies on
  the next start, so one that succeeds makes a restart needed.
- `plugins_view.py` and `add_plugin_dialog.py` present them; the view reads the catalog again whenever it is shown.

Reload loads the plugins again in this process; installs, updates and removals need the restart the view offers.
