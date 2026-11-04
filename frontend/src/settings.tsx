import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useGrants } from "./access";
import { apiFetch } from "./api";
import type { ApiError, SettingsChange, SettingsState } from "./generated/api";
import { COMMANDS, SETTINGS, type SettingKey } from "./generated/settings";

export interface SettingsApi extends SettingsState {
  /**
   * Stores *change* where each part lives and shows what the backend returns; resolves to the refusal (its `location`
   * is a setting key or `shortcuts.<id>`, empty when the backend could not be reached), else null.
   */
  save: (change: SettingsChange) => Promise<ApiError | null>;
  /**
   * Reads the settings from the backend again, for a change the backend made itself; does nothing without one. A
   * failed read keeps the settings already loaded.
   */
  reload: () => Promise<void>;
}

type Load =
  | { state: "loading" }
  | { state: "ready"; remote: SettingsState | null }
  | { state: "failed"; reason: string };

/** Provided by `SettingsProvider`; tests provide their own value. */
export const SettingsContext = createContext<SettingsApi | null>(null);

type Row = (typeof SETTINGS)[number];
type Values = SettingsState["values"];
type Shortcuts = SettingsState["shortcuts"];

/** The Personal settings and shortcut overrides this browser keeps when the backend does not keep them. */
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

/** Whether *value* is something this Personal row can hold; a stored value that is not is ignored. */
const holds = (row: Row, value: unknown) =>
  row.kind.type === "bool"
    ? typeof value === "boolean"
    : row.kind.type === "sticky"
      ? isSticky(value)
      : row.kind.type === "sync"
        ? isSync(value)
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

/** The part of *change* this browser keeps. */
function localOf(change: SettingsChange): Local {
  const values: Partial<Values> = {};
  for (const { key } of PERSONAL) values[key] = change.values[key];
  const shortcuts: Partial<Shortcuts> = {};
  for (const { id, key } of COMMANDS)
    if (change.shortcuts[id] !== key) shortcuts[id] = change.shortcuts[id];
  return { values, shortcuts };
}

/** The part of *change* that belongs on the server: every row that is not Personal. */
function serverValues(values: Values): Partial<Values> {
  const personal = new Set<SettingKey>(PERSONAL.map((row) => row.key));
  return Object.fromEntries(
    Object.entries(values).filter(([key]) => !personal.has(key as SettingKey)),
  );
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
 * Where each setting lives follows the grants. Personal settings and shortcuts come from the backend only when it
 * grants `preferences`, so a server-password holder or a link watcher never changes someone else's theme; otherwise
 * this browser keeps them. Server settings come from the backend for `configuration` holders. Children render once
 * the settings are read, with the theme and text size applied live to the document root; a failed read shows its reason instead.
 */
export function SettingsProvider({ children }: { children: ReactNode }) {
  const grants = useGrants();
  const keepsPreferences = grants.includes("preferences");
  const hasBackend = keepsPreferences || grants.includes("configuration");
  const [load, setLoad] = useState<Load>(
    hasBackend ? { state: "loading" } : { state: "ready", remote: null },
  );
  const [local, setLocal] = useState(readLocal);
  // Bumped when a reload or a save starts or finishes, so a reload answer that a later one overtook is dropped.
  const latest = useRef(0);
  useEffect(() => {
    if (!hasBackend) return;
    let current = true;
    request()
      .then((remote) => {
        if (current) setLoad({ state: "ready", remote });
      })
      .catch((error: unknown) => {
        if (current) setLoad({ state: "failed", reason: refusalOf(error).reason });
      });
    return () => {
      current = false;
    };
  }, [hasBackend]);
  const remote = load.state === "ready" ? load.remote : null;
  const mine = keepsPreferences ? null : local;
  const values = { ...DEFAULT_VALUES, ...remote?.values, ...mine?.values };
  useAppearance(values["ui.theme"] as string, values["ui.text_size"] as string);

  if (load.state === "failed")
    return <p role="alert">ax-devil cannot read its settings: {load.reason}</p>;
  if (load.state === "loading") return <main aria-busy="true" />;

  const save = async (change: SettingsChange) => {
    latest.current += 1;
    try {
      if (hasBackend) {
        // Without `preferences` the backend takes only the Server rows, and the shortcuts go back as they were read.
        const sent = keepsPreferences
          ? change
          : {
              values: serverValues(change.values),
              shortcuts: remote?.shortcuts ?? STANDARD_SHORTCUTS,
            };
        const replaced = await request({
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(sent),
        });
        latest.current += 1;
        setLoad({ state: "ready", remote: replaced });
      }
      if (!keepsPreferences) {
        const kept = localOf(change);
        window.localStorage.setItem(LOCAL_KEY, JSON.stringify(kept));
        setLocal(kept);
      }
      return null;
    } catch (error) {
      return refusalOf(error);
    }
  };
  const reload = async () => {
    if (!hasBackend) return;
    latest.current += 1;
    const started = latest.current;
    try {
      const remote = await request();
      if (latest.current === started) setLoad({ state: "ready", remote });
    } catch {
      // A failed refresh keeps the settings already read: the failed state would unmount the whole app.
    }
  };
  const shortcuts = mine
    ? { ...STANDARD_SHORTCUTS, ...mine.shortcuts }
    : (remote?.shortcuts ?? STANDARD_SHORTCUTS);
  return (
    <SettingsContext.Provider value={{ values, shortcuts, save, reload }}>
      {children}
    </SettingsContext.Provider>
  );
}

/** The stored settings, the effective shortcuts, and the way to store new ones; only inside a `SettingsProvider`. */
export function useSettings(): SettingsApi {
  const settings = useContext(SettingsContext);
  if (!settings) throw new Error("useSettings needs a SettingsProvider");
  return settings;
}
