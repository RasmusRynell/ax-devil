// Generated from api_types by make; do not edit.

import type { CommandId, SettingKey } from "../settings";

/**
 * What a failed request returns: the reason, shown where the user acted, and what it is about.
 */
export type ApiError = { 
/**
 * What is wrong, in words for the user.
 */
reason: string, 
/**
 * The setting key (`ui.theme`), `shortcuts.<command id>`, or file the reason is about.
 */
location: string, };

/**
 * Identifies a running backend, so the desktop shell and the development supervisor reuse only a compatible one.
 */
export type AppInfo = { 
/**
 * Always `"ax-devil"`.
 */
app: string, 
/**
 * The backend API version; a client that expects another one refuses this backend.
 */
api: number, 
/**
 * The package version.
 */
version: string, 
/**
 * Whether this is a development build that serves no frontend.
 */
development: boolean, 
/**
 * The backend process id.
 */
pid: number, 
/**
 * The desktop shell interface this backend's frontend needs; compare with the shell's own version.
 */
shell: number, 
/**
 * Whether the caller of this request is the Owner.
 */
owner: boolean, 
/**
 * Whether the caller is an Owner with Full access.
 */
fullAccess: boolean, };

/**
 * The body of `PUT /api/settings`: every setting and shortcut, which replace the stored ones. A masked
 * secret sent back keeps the stored secret.
 */
export type SettingsChange = { 
/**
 * Every setting by dotted key.
 */
values: Record<SettingKey, string | boolean | number | Array<Record<string, string>> | { enabled: boolean; timeout_ms: number | null; opacity: number } | { tolerance_ms: number | null } | { filter: Record<string, boolean>; ranges: Record<"score" | "confidence", { min: number | null; max: number | null }>; expression: string }>, 
/**
 * The key of every command; `null` when unbound.
 */
shortcuts: Record<CommandId, string | null>, };

/**
 * Where the settings are kept: the file's path on the backend's machine and its format version.
 */
export type SettingsFile = { 
/**
 * The config file's path.
 */
path: string, 
/**
 * The file format version.
 */
version: number, 
/**
 * The log file's path, when this backend writes one.
 */
log?: string, };

/**
 * Every setting and shortcut as the app uses them: the body of `GET /api/settings` and the reply to a save. The keys
 * and command ids are declared once, in the settings crate, and exported to `generated/settings.ts`.
 */
export type SettingsState = { 
/**
 * Every setting by dotted key, such as `"ui.theme"`, with the default when unset, a `$NAME` reference as written
 * and a literal secret masked.
 */
values: Record<SettingKey, string | boolean | number | Array<Record<string, string>> | { enabled: boolean; timeout_ms: number | null; opacity: number } | { tolerance_ms: number | null } | { filter: Record<string, boolean>; ranges: Record<"score" | "confidence", { min: number | null; max: number | null }>; expression: string }>, 
/**
 * What each non-secret setting among `values` that holds a `$NAME` reference gives as it is used (a number for a
 * whole-number setting), for a form that starts from the value. Only with Full access, since it is the server's
 * environment; absent for a setting without a reference.
 */
resolved?: Partial<Record<SettingKey, string | number>>, 
/**
 * The config file.
 */
file?: SettingsFile, 
/**
 * The key of every command once the user's changes apply; `null` when unbound.
 */
shortcuts: Record<CommandId, string | null>, };
