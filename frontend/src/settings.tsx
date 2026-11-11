import { createContext, useContext, useEffect, useLayoutEffect, type ReactNode } from "react";
import { useAccess } from "./access";
import { deviceStore, useDeviceValues, type DeviceValue } from "./deviceSettings";
import type { ApiError, LoginEdit, SettingsPatch, SettingsState } from "./generated/api";
import { fetchJson, refusalOf, useResource } from "./resource";
import { COMMANDS, SETTINGS, type CommandId, type SettingKey } from "./generated/settings";
import { type Login, secretStateOf } from "./secret";

/**
 * Every way to store settings resolves to the refusal (its `location` is a setting key or `shortcuts.<id>`, empty when
 * the backend could not be reached), else null. Only the keys named are written; any other keeps the value it has
 * now, whoever wrote it.
 */
export interface SettingsApi extends SettingsState {
  /** Stores one patch: several settings and shortcuts at once, as one write that is refused or taken whole. */
  apply: (patch: SettingsPatch) => Promise<ApiError | null>;
  /** Sets one setting; logins carry the passwords typed since, which the backend does not tell. */
  set: <K extends SettingKey>(
    key: K,
    value: SettingsState["values"][K] | Login[],
  ) => Promise<ApiError | null>;
  /** Puts one setting back to its default. */
  reset: (key: SettingKey) => Promise<ApiError | null>;
  /** Binds a command to *key*, or unbinds it with null. */
  setShortcut: (id: CommandId, key: string | null) => Promise<ApiError | null>;
  /** Puts a command back to its own key. */
  resetShortcut: (id: CommandId) => Promise<ApiError | null>;
}

/** The single-key ways to store, over `apply`. */
export function through(apply: SettingsApi["apply"]) {
  return {
    apply,
    set: (key: SettingKey, value: unknown) =>
      apply({ set: { [key]: value } as SettingsPatch["set"] }),
    reset: (key: SettingKey) => apply({ reset: [key] }),
    setShortcut: (id: CommandId, key: string | null) => apply({ shortcutsSet: { [id]: key } }),
    resetShortcut: (id: CommandId) => apply({ shortcutsReset: [id] }),
  };
}

/** Whether *patch* names nothing. */
export const isEmpty = (patch: SettingsPatch) =>
  Object.keys(patch.set ?? {}).length +
    (patch.reset?.length ?? 0) +
    Object.keys(patch.shortcutsSet ?? {}).length +
    (patch.shortcutsReset?.length ?? 0) ===
  0;

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** The patch that takes the settings *base* to *draft*: only the keys that differ. */
export function settingsPatch(base: SettingsState["values"], draft: SettingsState["values"]) {
  const set: Record<string, unknown> = {};
  for (const key of Object.keys(draft) as SettingKey[])
    if (!same(base[key], draft[key])) set[key] = draft[key];
  return (Object.keys(set).length ? { set } : {}) as SettingsPatch;
}

/** The patch that takes the shortcuts *base* to *draft*: a command back on its own key is reset, the rest set. */
export function shortcutsPatch(
  base: SettingsState["shortcuts"],
  draft: SettingsState["shortcuts"],
) {
  const shortcutsSet: Record<string, string | null> = {};
  const shortcutsReset: CommandId[] = [];
  for (const { id, key } of COMMANDS) {
    if (base[id] === draft[id]) continue;
    if (draft[id] === key) shortcutsReset.push(id);
    else shortcutsSet[id] = draft[id];
  }
  return {
    ...(Object.keys(shortcutsSet).length && { shortcutsSet }),
    ...(shortcutsReset.length && { shortcutsReset }),
  } as SettingsPatch;
}

/** Provided by `SettingsProvider`; tests provide their own value. */
export const SettingsContext = createContext<SettingsApi | null>(null);

type Row = (typeof SETTINGS)[number];
type Values = SettingsState["values"];
type Shortcuts = SettingsState["shortcuts"];

/**
 * The value of *row* when the backend has none: a list of records is built from its rows of field values, with the
 * ids the backend gives the defaults, and a secret is its state.
 */
function defaultOf(row: Row) {
  const kind = row.kind;
  if (kind.type === "records")
    return kind.default.map((values, index) => ({
      id: `default-${index}`,
      ...Object.fromEntries(
        kind.fields.map((field, at) => [
          field,
          field === "password" ? secretStateOf(values[at] ?? "", row.env) : values[at],
        ]),
      ),
    }));
  const value = "default" in kind ? kind.default : kind.type === "auto_or_whole" ? "auto" : "";
  return row.secret ? secretStateOf(typeof value === "string" ? value : "", row.env) : value;
}

/** Where a page that holds only a join link once kept its own Personal settings; it keeps none now. */
const RETIRED_KEY = "ax-devil.preferences";
const DEVICE_KEYS = new Set<string>(
  SETTINGS.filter((row) => row.scope === "device").map((row) => row.key),
);
export const DEFAULT_VALUES = Object.fromEntries(
  SETTINGS.map((row) => [row.key, defaultOf(row)]),
) as Values;
const STANDARD_SHORTCUTS = Object.fromEntries(
  COMMANDS.map((command) => [command.id, command.key]),
) as Shortcuts;

/** Puts the theme and text size on the document root; tokens.css selects the colours and the body size from them. */
export function applyAppearance(theme: string, textSize: string) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.textSize = textSize;
}

/** Applies the theme and text size to the document root whenever they change. */
export function useAppearance(theme: string, textSize: string) {
  useLayoutEffect(() => applyAppearance(theme, textSize), [theme, textSize]);
}

/** The logins as the backend takes them: a login keeps its saved password unless a new one was typed. */
export const loginsOnWire = (logins: readonly Login[]): LoginEdit[] =>
  logins.map(({ id, user, password }) => ({
    ...(id !== undefined && { id }),
    user,
    ...(typeof password === "string" && { password }),
  }));

/** Splits *patch* into what the device stores (its Device keys) and what the backend stores. */
function splitPatch(patch: SettingsPatch) {
  const entries = Object.entries(patch.set ?? {});
  const device = {
    set: entries.filter(([key]) => DEVICE_KEYS.has(key)),
    reset: (patch.reset ?? []).filter((key) => DEVICE_KEYS.has(key)),
  };
  const set = Object.fromEntries(
    entries
      .filter(([key]) => !DEVICE_KEYS.has(key))
      .map(([key, value]) => [key, Array.isArray(value) ? loginsOnWire(value as Login[]) : value]),
  );
  const reset = (patch.reset ?? []).filter((key) => !DEVICE_KEYS.has(key));
  // A part left out is not sent.
  const server = {
    ...patch,
    set: Object.keys(set).length ? set : undefined,
    reset: reset.length ? reset : undefined,
  } as SettingsPatch;
  return { device, server };
}

/**
 * An Owner's Personal and Server settings come from the backend and go to it; a page that holds only a join link
 * gets the defaults of those and cannot change them. Device settings live on this device for everyone, never on the
 * backend. Children render once the settings are read, with the theme and text size applied live to the document
 * root; a failed first read shows its reason instead. The settings are read again when the backend announces
 * another revision of them (see `useResource`); `App` reads `GET /api/info` on the same notices, since Full access
 * follows `server.password_full_access`.
 */
export function SettingsProvider({ children }: { children: ReactNode }) {
  const { owner } = useAccess();
  const device = useDeviceValues();
  const settings = useResource<SettingsState>("settings", owner ? "/api/settings" : null, {
    revisionOf: (state) => state.revision,
  });
  // A page that holds only a join link kept a copy of the Personal settings here; nothing stale stays behind.
  useEffect(() => {
    try {
      window.localStorage.removeItem(RETIRED_KEY);
    } catch {
      // Storage is blocked: nothing was kept.
    }
  }, []);
  const remote = settings.value ?? null;
  const values = { ...DEFAULT_VALUES, ...remote?.values, ...device };
  useAppearance(values["ui.theme"] as string, values["ui.text_size"] as string);

  if (owner && !remote) {
    return settings.error ? (
      <p role="alert">ax-devil cannot read its settings: {refusalOf(settings.error).reason}</p>
    ) : (
      <main aria-busy="true" />
    );
  }

  const apply = async (patch: SettingsPatch) => {
    const { device: mine, server } = splitPatch(patch);
    try {
      if (!isEmpty(server)) {
        if (!owner) return { reason: "Only the Owner can change this", location: "" };
        settings.apply(
          await fetchJson<SettingsState>("/api/settings", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(server),
          }),
        );
      }
      // After the backend took its part, so a refused save changes nothing here either.
      for (const [key, value] of mine.set) deviceStore.set(key as SettingKey, value as DeviceValue);
      for (const key of mine.reset) deviceStore.reset(key as SettingKey);
      return null;
    } catch (error) {
      return refusalOf(error);
    }
  };
  const shortcuts = remote?.shortcuts ?? STANDARD_SHORTCUTS;
  return (
    <SettingsContext.Provider
      value={{
        revision: remote?.revision ?? 0,
        values,
        resolved: remote?.resolved,
        file: remote?.file,
        shortcuts,
        ...through(apply),
      }}
    >
      {children}
    </SettingsContext.Provider>
  );
}

/** The stored settings, the effective shortcuts, and the ways to store new ones; only inside a `SettingsProvider`. */
export function useSettings(): SettingsApi {
  const settings = useContext(SettingsContext);
  if (!settings) throw new Error("useSettings needs a SettingsProvider");
  return settings;
}
