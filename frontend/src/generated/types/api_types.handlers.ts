// Generated from api_types::handlers by make; do not edit.

/**
 * One value a scan found.
 */
export type ChoiceInfo = { 
/**
 * What is stored in the settings.
 */
value: string, 
/**
 * What the user reads.
 */
label: string, 
/**
 * A second line about the value; may be empty.
 */
detail: string, };

/**
 * A menu entry and shortcut a plugin adds.
 */
export type CommandInfo = { 
/**
 * `<plugin id>.<command id>`.
 */
id: string, 
/**
 * What the user reads.
 */
title: string, 
/**
 * What it applies to: `app`, `item` or `lane`.
 */
on: string, 
/**
 * The default key, such as `Ctrl+Alt+E`.
 */
key?: string, 
/**
 * The id of the plugin's own decoder, task or view it runs.
 */
runs: string, };

/**
 * One thing a plugin lists: a handler, or one it lists that this build cannot use.
 */
export type ContributionRow = { 
/**
 * The manifest `kind`: `decoder`, `playlist_resolver`, `task`, `view` or `command`; for a skipped row whatever
 * the manifest says.
 */
kind: string, 
/**
 * The handler id; empty for a skipped row that has none.
 */
id: string, 
/**
 * What the user reads.
 */
name: string, 
/**
 * What else tells it apart: a decoder's file extensions, a view's place, a command's target. May be empty.
 */
detail: string, 
/**
 * Why it is not used; absent for a handler that runs.
 */
skipped?: string, };

/**
 * An overlay file decoder.
 */
export type DecoderInfo = { 
/**
 * The handler id that selects the decoder.
 */
handler: string, 
/**
 * What the user reads.
 */
display_name: string, 
/**
 * One sentence on what it reads; absent when it has none.
 */
description?: string, 
/**
 * The file name endings it reads: lowercase, with the dot. Empty means any file.
 */
extensions: Array<string>, 
/**
 * The options it takes, in the order they are shown; empty for a decoder without any.
 */
options: Array<FieldInfo>, 
/**
 * Whether it can decode a live stream's overlay, not only a file.
 */
live: boolean, };

/**
 * One setting of a resolver or plugin, or option of a decoder or task.
 */
export type FieldInfo = { 
/**
 * The key in the settings.
 */
key: string, 
/**
 * What the user reads.
 */
label: string, 
/**
 * What kind of value it holds, and so which control it gets.
 */
kind: FieldKindWire, 
/**
 * Whether resolving fails without it, once the default is filled in.
 */
required: boolean, 
/**
 * The value used when none is given; absent when there is none.
 */
default?: unknown, };

/**
 * What kind of value a setting holds.
 */
export type FieldKindWire = { "kind": "Folder" } | { "kind": "File", 
/**
 * Lowercase, with the dot.
 */
extensions: Array<string>, } | { "kind": "Text" } | { "kind": "Integer", 
/**
 * The smallest value allowed.
 */
min: bigint, } | { "kind": "Handler" } | { "kind": "DecoderOptions", 
/**
 * The key of the `Handler` field.
 */
handler_field: string, } | { "kind": "Scan", 
/**
 * The settings the scan reads, whose change invalidates its result.
 */
depends_on: Array<string>, 
/**
 * What a count-only scan says; absent for a scan whose values are picked.
 */
count?: ScanCountInfo, };

/**
 * The body of `GET /api/handlers`.
 */
export type HandlersInfo = { 
/**
 * The overlay file decoders.
 */
decoders: Array<DecoderInfo>, 
/**
 * The playlist resolvers.
 */
resolvers: Array<ResolverInfo>, };

/**
 * The body of `POST /api/plugins/install`.
 */
export type InstallRequest = { 
/**
 * The absolute path of the plugin's folder, or of its `ax-devil-plugin.json`.
 */
path: string, };

/**
 * A lane decoder or task a plugin provides.
 */
export type OperationInfo = { 
/**
 * The handler id.
 */
id: string, 
/**
 * `decoder` or `task`.
 */
kind: string, 
/**
 * What the user reads.
 */
name: string, 
/**
 * `(key, label)` of each lane it reads, in order.
 */
lanes: Array<[string, string]>, 
/**
 * Whether it is also given the video.
 */
video: boolean, 
/**
 * The options it takes, in the order they are shown.
 */
options: Array<FieldInfo>, };

/**
 * One plugin.
 */
export type PluginInfo = { 
/**
 * Where it comes from.
 */
source: PluginSource, 
/**
 * Whether it loaded.
 */
status: PluginStatus, };

/**
 * Where a plugin comes from.
 */
export type PluginSource = { "kind": "built_in" } | { "kind": "installed", 
/**
 * The manifest file the registry names.
 */
manifest: string, 
/**
 * The folder the manifest is in.
 */
folder: string, };

/**
 * Whether a plugin can be used.
 */
export type PluginStatus = { "state": "loaded", 
/**
 * The plugin id.
 */
id: string, 
/**
 * What the user reads.
 */
name: string, 
/**
 * Free text from the manifest; the app's version for a built-in.
 */
version: string, 
/**
 * What it provides, in the order it lists them, then what it lists that this build cannot use.
 */
rows: Array<ContributionRow>, 
/**
 * The form of the plugin's own settings; empty when it has none.
 */
settings: Array<FieldInfo>, 
/**
 * The settings the plugin runs with: the stored values with the defaults filled in.
 */
values: Record<string, unknown>, 
/**
 * The pages it shows.
 */
views: Array<ViewInfo>, 
/**
 * The menu entries and shortcuts it adds.
 */
commands: Array<CommandInfo>, 
/**
 * The lane decoders and tasks it provides, which take lanes and options.
 */
operations: Array<OperationInfo>, } | { "state": "failed", 
/**
 * What is wrong, naming the manifest field when there is one.
 */
reason: string, };

/**
 * The body of `GET /api/plugins`: the built-in plugins, then the installed ones, whether or not they loaded.
 */
export type PluginsInfo = { 
/**
 * The built-ins, then one per registry entry in install order.
 */
plugins: Array<PluginInfo>, 
/**
 * Why the registry itself could not be used, in which case no installed plugin loaded.
 */
problem?: string, };

/**
 * The body of `POST /api/plugins/remove`.
 */
export type RemoveRequest = { 
/**
 * The id of an installed plugin, or the absolute path of its manifest or folder.
 */
target: string, };

/**
 * The body of `POST /api/resolvers/{id}/resolve`.
 */
export type ResolveRequest = { 
/**
 * The Session of the window that asks; only a seat of that Session can add the resolved item.
 */
session: string, 
/**
 * The settings the resolver's schema declares.
 */
settings: Record<string, unknown>, };

/**
 * The answer to a resolve: the resolved item, held by the backend until a `DocCommand::AddResolved` adds it.
 */
export type ResolvedInfo = { 
/**
 * The item's id.
 */
item: string, };

/**
 * A playlist resolver and the form its settings take.
 */
export type ResolverInfo = { 
/**
 * The id the resolver is named by.
 */
id: string, 
/**
 * What the user reads.
 */
display_name: string, 
/**
 * One sentence on what the resolver does.
 */
description: string, 
/**
 * The name of the installed plugin that provides it; absent for a built-in.
 */
plugin?: string, 
/**
 * The settings, in the order they are shown.
 */
fields: Array<FieldInfo>, };

/**
 * The sentences of a count-only scan.
 */
export type ScanCountInfo = { 
/**
 * Shown while a setting the scan reads is empty.
 */
missing: string, 
/**
 * Shown when the scan found something; `{count}` stands for how many.
 */
found: string, 
/**
 * Shown when the scan found nothing.
 */
none: string, };

/**
 * The body of `POST /api/resolvers/{id}/scan`.
 */
export type ScanRequest = { 
/**
 * The `Scan` field to find values for.
 */
field: string, 
/**
 * The settings entered so far.
 */
settings: Record<string, unknown>, };

/**
 * The body of `POST /api/plugins/settings`.
 */
export type SettingsRequest = { 
/**
 * The id of an installed plugin, or the absolute path of its manifest or folder.
 */
plugin: string, 
/**
 * The plugin's settings, checked against its form; replaces the stored ones.
 */
values: Record<string, unknown>, };

/**
 * The body of `POST /api/plugins/update`.
 */
export type UpdateRequest = { 
/**
 * The id of an installed plugin, or the absolute path of its manifest or folder.
 */
target: string, };

/**
 * A page a plugin shows.
 */
export type ViewInfo = { 
/**
 * The view id, unique within its plugin.
 */
id: string, 
/**
 * What the user reads.
 */
title: string, 
/**
 * The name of an icon.
 */
icon?: string, 
/**
 * Where it is shown: `sidebar` or `video`.
 */
place: string, 
/**
 * Where the page is served, `/views/<token>/<document>`. The token lasts until the plugins reload.
 */
url: string, };
