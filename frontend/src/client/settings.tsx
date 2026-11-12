import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  type ReactNode,
} from "react";
import { useAccess } from "./access";
import { ask } from "./ask";
import {
  deviceStore,
  useDeviceValues,
  useSavedDeviceValues,
  type DeviceValue,
} from "./deviceSettings";
import { reasonOf } from "./errors";
import type { ErrorWire, LoginEdit, SettingsPatch, SettingsState } from "../generated/api";
import { refusalOf, useResource } from "./resource";
import { COMMANDS, SETTINGS, type CommandId, type SettingKey } from "../generated/settings";
import { type Login, secretStateOf } from "./secret";

/**
 * Every way to store settings resolves to the refusal (its `location` is a setting key or `shortcuts.<id>`, absent when
 * the backend could not be reached), else null. Only the keys named are written; any other keeps the value it has
 * now, whoever wrote it.
 */
export interface SettingsApi extends SettingsState {
  /** Validates a default choice; the newest request across menus wins. */
  saveCatalogDefault: (
    id: string,
    validate: () => Promise<number | undefined>,
  ) => Promise<ErrorWire | null>;
  /** Baseline for edits: confirmed values without Quick Setup previews; Device persistence is best effort. */
  savedValues: SettingsState["values"];
  /** Previews a Device choice without storing it; Quick Setup keeps it on refusal. */
  previewDevice: (key: SettingKey, value: DeviceValue) => void;
  /** Saves the server part atomically, then confirms Device changes with best-effort persistence; not atomic across scopes. */
  apply: (patch: SettingsPatch) => Promise<ErrorWire | null>;
  /** Sets one setting; logins carry the passwords typed since, which the backend does not tell. */
  set: <K extends SettingKey>(
    key: K,
    value: SettingsState["values"][K] | Login[],
  ) => Promise<ErrorWire | null>;
  /** Puts one setting back to its default. */
  reset: (key: SettingKey) => Promise<ErrorWire | null>;
  /** Binds a command to *key*, or unbinds it with null. */
  setShortcut: (id: CommandId, key: string | null) => Promise<ErrorWire | null>;
  /** Puts a command back to its own key. */
  resetShortcut: (id: CommandId) => Promise<ErrorWire | null>;
}

/** The single-key ways to store, over `apply`. */
export function through(apply: SettingsApi["apply"]) {
  return {
    apply,
    previewDevice: (key: SettingKey, value: DeviceValue) => {
      if (DEVICE_KEYS.has(key)) deviceStore.preview(key, value);
    },
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

/** Chosen values that differ from the confirmed baseline or the values the dialog initially showed. */
export function settingsPatch(
  base: SettingsState["values"],
  draft: SettingsState["values"],
  shown = base,
) {
  const set: Record<string, unknown> = {};
  for (const key of Object.keys(draft) as SettingKey[])
    if (!same(base[key], draft[key]) || !same(shown[key], draft[key])) set[key] = draft[key];
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
  const savedDevice = useSavedDeviceValues();
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
      <p role="alert">ax-devil cannot read its settings: {reasonOf(refusalOf(settings.error))}</p>
    ) : (
      <main aria-busy="true" />
    );
  }

  const apply = async (patch: SettingsPatch): Promise<ErrorWire | null> => {
    const { device: mine, server } = splitPatch(patch);
    const commitDevice = deviceStore.prepare(mine.set as [SettingKey, DeviceValue][], mine.reset);
    if (!isEmpty(server)) {
      if (!owner)
        return { code: "Forbidden", params: {}, reason: "Only the Owner can change this" };
      const answer = await ask<SettingsState>("/api/settings", { method: "PATCH", json: server });
      if (!answer.ok) return answer.error;
      settings.apply(answer.value);
    }
    // After the backend took its part, so a refused save changes nothing here either.
    commitDevice();
    return null;
  };
  const shortcuts = remote?.shortcuts ?? STANDARD_SHORTCUTS;
  return (
    <SettingsScope
      value={{
        revision: remote?.revision ?? 0,
        values,
        savedValues: { ...DEFAULT_VALUES, ...remote?.values, ...savedDevice },
        resolved: remote?.resolved,
        file: remote?.file,
        shortcuts,
        ...through(apply),
      }}
    >
      {children}
    </SettingsScope>
  );
}

/** Shares catalog validation ordering across every menu using the settings owner. */
export function SettingsScope({
  value,
  children,
}: {
  value: Omit<SettingsApi, "saveCatalogDefault">;
  children: ReactNode;
}) {
  const current = useRef(value.apply);
  current.current = value.apply;
  const newest = useRef(0);
  useEffect(
    () => () => {
      newest.current++;
    },
    [],
  );
  const apply: SettingsApi["apply"] = (patch) => {
    if (
      "overlays.default_catalog" in (patch.set ?? {}) ||
      patch.reset?.includes("overlays.default_catalog")
    )
      newest.current++;
    return current.current(patch);
  };
  const saveCatalogDefault: SettingsApi["saveCatalogDefault"] = async (id, validate) => {
    const request = ++newest.current;
    try {
      const revision = await validate();
      if (request !== newest.current) return null;
      return await apply({ set: { "overlays.default_catalog": id }, catalogRevision: revision });
    } catch (error) {
      return request === newest.current ? refusalOf(error) : null;
    }
  };
  return (
    <SettingsContext value={{ ...value, ...through(apply), saveCatalogDefault }}>
      {children}
    </SettingsContext>
  );
}

/** The effective and confirmed settings, the effective shortcuts, and the ways to store new ones; only inside a `SettingsProvider`. */
export function useSettings(): SettingsApi {
  const settings = useContext(SettingsContext);
  if (!settings) throw new Error("useSettings needs a SettingsProvider");
  return settings;
}
