# Plugins

ax-devil reads other overlay formats through **converters**: programs in any language that you install and ax-devil
runs on an overlay file to get its Scenes. Writing one, with an agent or by hand, is in the
[write-plugin skill](../.agents/skills/write-plugin/SKILL.md); the contract is its
[reference](../.agents/skills/write-plugin/reference.md). The decoders and playlist resolvers that ship with ax-devil
are built in and not plugins you install.

## Installing

`ax-devil plugins install FOLDER` adds a converter's folder (the one holding `ax-devil-converter.json`) to your list,
`plugins.json` under `$XDG_DATA_HOME/ax-devil` (`~/.local/share/ax-devil`). Only that list loads converters; a folder
is never picked up for being there. Install and remove apply the next time ax-devil starts; removing leaves the files.
There is no update command: edit the folder in place (a changed script runs again the next time its file opens; a
changed manifest needs a restart) or remove and install again. `plugins --help` lists the rest, including the check
that runs a converter on a file and says what came out.

A converter that fails to load, or fails while it runs, never stops ax-devil: the lane shows what was read and the
reason, and `plugins list` says why one did not load.

## Trust

A converter is code you install. It runs as you, can read your files and reach the network, and ax-devil does not
sandbox it. ax-devil gives it only the overlay path, the options its manifest declares, a short
allowlist of environment variables (none of ax-devil's credentials) and an empty private working folder, and kills it
and, except on Windows, everything it started when the lane no longer needs it. A workspace from someone else can
make ax-devil run an installed converter on a file it names, as it does with a built-in decoder. Install only
converters you trust.
