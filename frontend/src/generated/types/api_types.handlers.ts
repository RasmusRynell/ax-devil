// Generated from api_types::handlers by make; do not edit.

import type { Field } from "./handler_api.form";

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
options: Array<Field>, 
/**
 * Whether it can decode a live stream's overlay, not only a file.
 */
live: boolean, };

/**
 * What a handler does.
 */
export type HandlerKind = "decoder" | "playlist_resolver";

/**
 * One handler a plugin provides.
 */
export type HandlerSummary = { 
/**
 * What it does.
 */
kind: HandlerKind, 
/**
 * The id that selects it.
 */
id: string, 
/**
 * What the user reads.
 */
name: string, 
/**
 * The file extensions a decoder reads, lowercase with the dot; empty for the other kinds.
 */
extensions: Array<string>, };

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
 * What it provides, in the order it lists them.
 */
handlers: Array<HandlerSummary>, 
/**
 * The handlers it lists that this build cannot run, such as a kind added by a newer ax-devil.
 */
skipped: Array<SkippedHandlerInfo>, } | { "state": "failed", 
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
fields: Array<Field>, };

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
 * A handler of a plugin that is listed but not used.
 */
export type SkippedHandlerInfo = { 
/**
 * The kind the manifest gives.
 */
kind: string, 
/**
 * The handler id, when the manifest gives one.
 */
id?: string, 
/**
 * Why it is not used.
 */
reason: string, };

/**
 * The body of `POST /api/plugins/update`.
 */
export type UpdateRequest = { 
/**
 * The id of an installed plugin, or the absolute path of its manifest or folder.
 */
target: string, };
