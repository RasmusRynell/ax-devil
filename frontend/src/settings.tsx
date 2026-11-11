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
import type { ApiError, AppInfo, SettingsPatch, SettingsState } from "./generated/api";
import { COMMANDS, SETTINGS, type CommandId, type SettingKey } from "./generated/settings";
import { MEASURES } from "./present";

/**
 * Every way to store settings resolves to the refusal (its `location` is a setting key or `shortcuts.<id>`, empty when
 * the backend could not be reached), else null. Only the keys named are written; any other keeps the value it has
 * now, whoever wrote it.
 */
export interface SettingsApi extends SettingsState {
  /** Stores one patch: several settings and shortcuts at once, as one write that is refused or taken whole. */
  apply: (patch: SettingsPatch) => Promise<ApiError | null>;
  /** Sets one setting. */
  set: <K extends SettingKey>(
    key: K,
    value: SettingsState["values"][K],
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
export const SettingsNoticeContext = createContext<(revision: number) => void>(() => {});

type Load =
  | { state: "loading" }
  | { state: "ready"; remote: SettingsState | null }
  | { state: "failed"; reason: string };

/** Provided by `SettingsProvider`; tests provide their own value. */
export const SettingsContext = createContext<SettingsApi | null>(null);

type Row = (typeof SETTINGS)[number];
type Values = SettingsState["values"];
type Shortcuts = SettingsState["shortcuts"];

/** The Personal settings and shortcut overrides a page without a backend of its own keeps in this browser. */
interface Local {
  values: Partial<Values>;
  /** Only the commands whose key differs from the standard one. */
  shortcuts: Partial<Shortcuts>;
}

/** The value of *row* when the backend has none: a list of records is built from its rows of field values. */
function defaultOf(row: Row) {
  const kind = row.kind;
  if (kind.type === "records")
    return kind.default.map((values) =>
      Object.fromEntries(kind.fields.map((field, at) => [field, values[at]])),
    );
  return "default" in kind ? kind.default : kind.type === "auto_or_whole" ? "auto" : "";
}

const LOCAL_KEY = "ax-devil.preferences";
const PERSONAL = SETTINGS.filter((row) => row.scope === "personal");
export const DEFAULT_VALUES = Object.fromEntries(
  SETTINGS.map((row) => [row.key, defaultOf(row)]),
) as Values;
const STANDARD_SHORTCUTS = Object.fromEntries(
  COMMANDS.map((command) => [command.id, command.key]),
) as Shortcuts;

/** Whether *value* is null or a whole number of milliseconds the backend accepts. */
const isMsOrNull = (value: unknown) =>
  value === null ||
  (typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 100_000);

/** Whether *value* is sticky settings in range: `enabled`, a whole `timeout_ms` or null, an `opacity` from 0 to 1. */
function isSticky(value: unknown) {
  const { enabled, timeout_ms: timeout, opacity } = (value ?? {}) as Record<string, unknown>;
  return (
    typeof enabled === "boolean" &&
    isMsOrNull(timeout) &&
    typeof opacity === "number" &&
    opacity >= 0 &&
    opacity <= 1
  );
}

/** Whether *value* is a sync default: only a `tolerance_ms` that is null (exact) or whole and in range. */
const isSync = (value: unknown) =>
  typeof value === "object" &&
  value !== null &&
  Object.keys(value).join() === "tolerance_ms" &&
  isMsOrNull((value as { tolerance_ms: unknown }).tolerance_ms);

/** Whether *value* is an object filter: class switches, the ranges of score and confidence, and an expression. */
function isObjectFilter(value: unknown) {
  const { filter, ranges, expression } = (value ?? {}) as Record<string, unknown>;
  const isBound = (bound: unknown) => bound === null || typeof bound === "number";
  return (
    typeof filter === "object" &&
    filter !== null &&
    Object.values(filter).every((on) => typeof on === "boolean") &&
    MEASURES.every((measure) => {
      const { min, max } = ((ranges as Record<string, unknown> | null)?.[measure] ?? {}) as Record<
        string,
        unknown
      >;
      return isBound(min) && isBound(max);
    }) &&
    typeof expression === "string"
  );
}

/** Whether *value* is something this Personal row can hold; a stored value that is not is ignored. */
const holds = (row: Row, value: unknown) =>
  row.kind.type === "bool"
    ? typeof value === "boolean"
    : row.kind.type === "sticky"
      ? isSticky(value)
      : row.kind.type === "sync"
        ? isSync(value)
        : row.kind.type === "object_filter"
          ? isObjectFilter(value)
          : row.kind.type === "text"
            ? typeof value === "string"
            : row.kind.type === "records"
              ? Array.isArray(value)
              : row.kind.type === "choice" && row.kind.options.some(([name]) => name === value);

/** What this browser kept: only what the Personal rows and commands still accept, ignoring anything unreadable. */
function readLocal(): Local {
  const local: Local = { values: {}, shortcuts: {} };
  try {
    const kept = JSON.parse(window.localStorage.getItem(LOCAL_KEY) ?? "{}") as Partial<Local>;
    for (const row of PERSONAL) {
      const value = kept.values?.[row.key];
      if (holds(row, value)) Object.assign(local.values, { [row.key]: value });
    }
    for (const { id } of COMMANDS) {
      const key = kept.shortcuts?.[id];
      if (key === null || typeof key === "string") local.shortcuts[id] = key;
    }
  } catch {
    // Nothing usable was kept: the defaults apply.
  }
  return local;
}

/** What this browser keeps once *patch* applies to *kept*: only Personal rows and commands. */
function applyLocal(kept: Local, patch: SettingsPatch): Local {
  const values = { ...kept.values };
  const shortcuts = { ...kept.shortcuts };
  for (const { key } of PERSONAL) {
    if (patch.set && key in patch.set) Object.assign(values, { [key]: patch.set[key] });
    if (patch.reset?.includes(key)) delete values[key];
  }
  for (const { id } of COMMANDS) {
    if (patch.shortcutsSet && id in patch.shortcutsSet) shortcuts[id] = patch.shortcutsSet[id];
    if (patch.shortcutsReset?.includes(id)) delete shortcuts[id];
  }
  return { values, shortcuts };
}

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

/**
 * An Owner's settings all come from the backend and go to it. A page that holds only a join link has no settings
 * there: this browser keeps its Personal ones. Children render once the settings are read, with the theme and text
 * size applied live to the document root; a failed read shows its reason instead. The settings are read again only
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
  const [local, setLocal] = useState(readLocal);
  // The revision of the settings held and of the last info asked for; a reply older than what is held is dropped.
  const held = useRef({ settings: 0, info: 0 });
  const take = useCallback((remote: SettingsState) => {
    if (remote.revision < held.current.settings) return;
    held.current.settings = remote.revision;
    setLoad({ state: "ready", remote });
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
    (revision: number) => {
      if (revision > held.current.info) {
        held.current.info = revision;
        apiFetch("/api/info")
          .then(async (response) => response.ok && onInfo((await response.json()) as AppInfo))
          // A failed read keeps what is held: the failed state would unmount the whole app.
          .catch(() => {});
      }
      if (revision > held.current.settings) request().then(take, () => {});
    },
    [take, onInfo],
  );
  const remote = load.state === "ready" ? load.remote : null;
  const mine = owner ? null : local;
  const values = { ...DEFAULT_VALUES, ...remote?.values, ...mine?.values };
  useAppearance(values["ui.theme"] as string, values["ui.text_size"] as string);

  if (load.state === "failed")
    return <p role="alert">ax-devil cannot read its settings: {load.reason}</p>;
  if (load.state === "loading") return <main aria-busy="true" />;

  const apply = async (patch: SettingsPatch) => {
    try {
      if (owner) {
        take(
          await request({
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(patch),
          }),
        );
      } else {
        const kept = applyLocal(readLocal(), patch);
        window.localStorage.setItem(LOCAL_KEY, JSON.stringify(kept));
        setLocal(kept);
      }
      return null;
    } catch (error) {
      return refusalOf(error);
    }
  };
  const shortcuts = mine
    ? { ...STANDARD_SHORTCUTS, ...mine.shortcuts }
    : (remote?.shortcuts ?? STANDARD_SHORTCUTS);
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
