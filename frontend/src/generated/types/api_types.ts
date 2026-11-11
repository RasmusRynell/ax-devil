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
 * The setting key (`camera.host`), `shortcuts.<command id>`, or file the reason is about.
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
 * The backend API version, see [`api_version`]; a client that expects another one refuses this backend.
 */
api: string, 
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
 * A login of the `scan.logins` setting as a client sends it, in the list that is saved whole and in order. With an
 * `id` it is that saved login, and a missing `password` keeps its saved one; without an `id` it is a new login, which
 * has a `password`. An unknown `id` is refused, and a saved login left out is removed.
 */
export type LoginEdit = { 
/**
 * The saved login this is.
 */
id?: string, 
/**
 * The user name, as written.
 */
user: string, 
/**
 * The new password, as written.
 */
password?: string, };

/**
 * A login of the `scan.logins` setting as a client reads it.
 */
export type LoginView = { 
/**
 * Names the login for as long as it is saved; a save refers to it by this.
 */
id: string, 
/**
 * The user name, as written.
 */
user: string, 
/**
 * The password.
 */
password: SecretState, };

/**
 * What a client learns of a secret: whether it is set and, for a `$NAME` reference, its name. Never the literal, its
 * length or a mask. A client changes a secret by naming it in a [`SettingsPatch`].
 */
export type SecretState = { "state": "unset" } | { "state": "literal" } | { "state": "reference", 
/**
 * The variable's name, without the `$`.
 */
name: string, };

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
 * The body of `PATCH /api/settings`: the keys to set and to reset. A key that is not named is not touched, so the
 * last write to a key wins. A secret is changed only by naming it; the logins are one list of [`LoginEdit`].
 */
export type SettingsPatch = { 
/**
 * The settings to set by dotted key.
 */
set?: Partial<Record<SettingKey, string | boolean | number | Array<LoginEdit> | { enabled: boolean; timeout_ms: number | null; opacity: number } | { tolerance_ms: number | null } | { filter: Record<string, boolean>; ranges: Record<"score" | "confidence", { min: number | null; max: number | null }>; expression: string }>>, 
/**
 * The settings to put back to their default.
 */
reset?: Array<SettingKey>, 
/**
 * The commands to bind: a key, or `null` to unbind.
 */
shortcutsSet?: Partial<Record<CommandId, string | null>>, 
/**
 * The commands to put back to their own key.
 */
shortcutsReset?: Array<CommandId>, };

/**
 * Every setting and shortcut as the app uses them: the body of `GET /api/settings` and the reply to a save. The keys
 * and command ids are declared once, in the settings crate, and exported to `generated/settings.ts`.
 */
export type SettingsState = { 
/**
 * Rises with every write to the settings; a state with a lower one is older. The `Changed` notice of the
 * settings topic on the Session socket carries the same number.
 */
revision: number, 
/**
 * Every setting by dotted key, such as `"camera.host"`, with the default when unset and a `$NAME` reference as
 * written. A secret, and the `password` of a login, is a [`SecretState`]: never the literal.
 */
values: Record<SettingKey, string | boolean | number | SecretState | Array<LoginView> | { enabled: boolean; timeout_ms: number | null; opacity: number } | { tolerance_ms: number | null } | { filter: Record<string, boolean>; ranges: Record<"score" | "confidence", { min: number | null; max: number | null }>; expression: string }>, 
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
