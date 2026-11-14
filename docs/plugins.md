# Plugins

ax-devil reads other overlay formats through **plugins**: programs in any language that you install and ax-devil
runs on an overlay file or a camera's live metadata to get its Scenes, or on a folder to get playlists. A plugin lists the **handlers** it
provides in its manifest: decoders, playlist resolvers, tasks, views and commands, all run from its one `program`. Writing one, with an agent or by hand, is in the
[write-plugin skill](../.agents/skills/write-plugin/SKILL.md); the contract is its
[reference](../.agents/skills/write-plugin/reference.md). The decoders and playlist resolvers that ship with ax-devil
are built in and not plugins you install.

## Installing

Only the list in `plugins.json` under `$XDG_DATA_HOME/ax-devil` (`~/.local/share/ax-devil`) loads plugins; a folder
is never picked up for being there. A running ax-devil follows the list and each listed manifest within a second or
two, for files opened from then on. Removing a plugin leaves its files. `ax-devil plugins --help` lists the commands.

A plugin that fails to load, or fails while it runs, never stops ax-devil: the lane shows what was read and the
reason, and `plugins list` says why one did not load.

## Trust

A plugin is code you install. It runs as you, can read your files and reach the network, and ax-devil does not
sandbox it. ax-devil gives it only the overlay path (or the records of a file or live source), the lane files and video a lane
reader (a derived lane's decoder or a task) declares, or the settings a resolver is given, the options and settings its manifest declares (taken literally, never
expanded), a short
allowlist of environment variables (none of ax-devil's credentials) and an empty private working folder, and kills it
and, except on Windows, everything it started when the lane no longer needs it. A workspace from someone else can
make ax-devil run an installed decoder on a file it names, or an installed resolver with settings it chooses, and
ax-devil then opens the paths the resolver returns. A view is a page of the plugin in a frame that cannot reach
ax-devil's own data, but it gets the whole bus and can leak what it is given: the lanes and playhead it reads can be
handed to a task of its plugin, whose program can send them anywhere. An Owner without Full access can run any
installed task with options they choose. Install only plugins you trust.
