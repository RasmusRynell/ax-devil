import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useAccess } from "./access";
import { apiFetch } from "./api";
import { deviceStore, useDeviceValues, type DeviceValue } from "./deviceSettings";
import type { ApiError, AppInfo, LoginEdit, SettingsPatch, SettingsState } from "./generated/api";
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

/** The session connection tells this when the backend says settings changed; `SettingsProvider` provides it. */
export const SettingsNoticeContext = createContext<(revision: number, fresh?: boolean) => void>(
  () => {},
);

type Load =
  | { state: "loading" }
  | { state: "ready"; remote: SettingsState | null }
  | { state: "failed"; reason: string };

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

async function request(init?: RequestInit): Promise<SettingsState> {
  const response = await apiFetch("/api/settings", init);
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw error ?? { reason: `the backend answered ${response.status}`, location: "" };
  }
  return (await response.json()) as SettingsState;
}

const refusalOf = (error: unknown): ApiError =>
  typeof error === "object" && error !== null && "reason" in error
    ? (error as ApiError)
    : { reason: error instanceof Error ? error.message : String(error), location: "" };

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
 * root; a failed read shows its reason instead. The settings are read again only
 * when the session connection reports a revision above the one held, together with `GET /api/info`, which `onInfo`
 * receives so that Full access follows `server.password_full_access`.
 */
export function SettingsProvider({
  children,
  onInfo,
}: {
  children: ReactNode;
  onInfo: (info: AppInfo) => void;
}) {
  const { owner } = useAccess();
  const [load, setLoad] = useState<Load>(
    owner ? { state: "loading" } : { state: "ready", remote: null },
  );
  const device = useDeviceValues();
  // The revision of the settings held and of the last info asked for, and how many infos were asked for; a reply
  // older than what is held is dropped, and so is an info reply once a newer info was asked for.
  const held = useRef({ settings: 0, info: 0, asks: 0 });
  const take = useCallback((remote: SettingsState) => {
    if (remote.revision < held.current.settings) return;
    held.current.settings = remote.revision;
    // The first state taken is the one the info was read with.
    if (held.current.info === 0) held.current.info = remote.revision;
    setLoad({ state: "ready", remote });
  }, []);
  // A page that holds only a join link kept a copy of the Personal settings here; nothing stale stays behind.
  useEffect(() => {
    try {
      window.localStorage.removeItem(RETIRED_KEY);
    } catch {
      // Storage is blocked: nothing was kept.
    }
  }, []);
  useEffect(() => {
    if (!owner) return;
    let current = true;
    request()
      .then((remote) => {
        if (current) take(remote);
      })
      .catch((error: unknown) => {
        if (current) setLoad({ state: "failed", reason: refusalOf(error).reason });
      });
    return () => {
      current = false;
    };
  }, [owner, take]);
  const noticed = useCallback(
    (revision: number, fresh = false) => {
      // A restarted backend may count from a lower revision (the clock set back): the first notice of a new
      // connection resets what is held only then, so a reconnect at the same revision fetches nothing.
      if (fresh && revision < held.current.settings)
        held.current = { ...held.current, settings: 0, info: 0 };
      if (revision > held.current.info) {
        held.current.info = revision;
        const ask = ++held.current.asks;
        apiFetch("/api/info")
          .then(async (response) => {
            const info = response.ok ? ((await response.json()) as AppInfo) : null;
            if (info && ask === held.current.asks) onInfo(info);
          })
          // A failed read keeps what is held: the failed state would unmount the whole app.
          .catch(() => {});
      }
      if (revision > held.current.settings) request().then(take, () => {});
    },
    [take, onInfo],
  );
  const remote = load.state === "ready" ? load.remote : null;
  const values = { ...DEFAULT_VALUES, ...remote?.values, ...device };
  useAppearance(values["ui.theme"] as string, values["ui.text_size"] as string);

  if (load.state === "failed")
    return <p role="alert">ax-devil cannot read its settings: {load.reason}</p>;
  if (load.state === "loading") return <main aria-busy="true" />;

  const apply = async (patch: SettingsPatch) => {
    const { device: mine, server } = splitPatch(patch);
    try {
      if (!isEmpty(server)) {
        if (!owner) return { reason: "Only the Owner can change this", location: "" };
        take(
          await request({
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
    <SettingsNoticeContext.Provider value={noticed}>
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
    </SettingsNoticeContext.Provider>
  );
}

/** The stored settings, the effective shortcuts, and the ways to store new ones; only inside a `SettingsProvider`. */
export function useSettings(): SettingsApi {
  const settings = useContext(SettingsContext);
  if (!settings) throw new Error("useSettings needs a SettingsProvider");
  return settings;
}
