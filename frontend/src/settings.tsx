import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import { useGrants } from "./access";
import { apiFetch } from "./api";
import type { ApiError, Settings } from "./generated/api";

export const THEMES = ["auto", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

interface SettingsApi {
  theme: Theme;
  /** Stores *next* where the settings live and shows what it returns; resolves to the refusal reason, else null. */
  save: (next: Theme) => Promise<string | null>;
}

type Load =
  | { state: "loading" }
  | { state: "ready"; stored: Settings }
  | { state: "failed"; reason: string };

const SettingsContext = createContext<SettingsApi | null>(null);

const LOCAL_KEY = "ax-devil.preferences";
// The Personal rows of crates/settings/src/table.rs with their defaults.
const PERSONAL_DEFAULTS: Settings = { "ui.theme": "auto", "ui.quick_setup_done": false };

/** Personal settings kept in this browser: what was stored over the defaults, ignoring anything unreadable. */
function readLocal(): Settings {
  try {
    const kept = JSON.parse(window.localStorage.getItem(LOCAL_KEY) ?? "{}") as Settings;
    const theme = THEMES.includes(kept["ui.theme"] as Theme) ? kept["ui.theme"] : undefined;
    return { ...PERSONAL_DEFAULTS, ...(theme && { "ui.theme": theme }) };
  } catch {
    return PERSONAL_DEFAULTS;
  }
}

async function request(init?: RequestInit): Promise<Settings> {
  const response = await apiFetch("/api/settings", init);
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(error?.reason ?? `the backend answered ${response.status}`);
  }
  return (await response.json()) as Settings;
}

const reasonOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

/**
 * Personal settings come from the backend only when it grants `preferences`, so a server-password holder or a
 * link watcher never changes someone else's theme; otherwise this browser keeps them. Children render once they
 * are read, with the theme applied live to the document root; a failed read shows its reason instead.
 */
export function SettingsProvider({ children }: { children: ReactNode }) {
  const onBackend = useGrants().includes("preferences");
  const [load, setLoad] = useState<Load>(() =>
    onBackend ? { state: "loading" } : { state: "ready", stored: readLocal() },
  );
  useEffect(() => {
    if (!onBackend) return;
    let current = true;
    request()
      .then((stored) => {
        if (current) setLoad({ state: "ready", stored });
      })
      .catch((error: unknown) => {
        if (current) setLoad({ state: "failed", reason: reasonOf(error) });
      });
    return () => {
      current = false;
    };
  }, [onBackend]);
  const stored = load.state === "ready" ? load.stored : null;
  const theme = stored?.["ui.theme"] as Theme | undefined;
  useLayoutEffect(() => {
    // tokens.css selects the color scheme from this attribute.
    if (theme) document.documentElement.dataset.theme = theme;
  }, [theme]);

  if (load.state === "failed")
    return <p role="alert">ax-devil cannot read its settings: {load.reason}</p>;
  if (!stored || !theme) return <main aria-busy="true" />;

  const save = async (next: Theme) => {
    try {
      const changed = { ...stored, "ui.theme": next };
      if (!onBackend) window.localStorage.setItem(LOCAL_KEY, JSON.stringify(changed));
      const replaced = onBackend
        ? await request({
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(changed),
          })
        : changed;
      setLoad({ state: "ready", stored: replaced });
      return null;
    } catch (error) {
      return reasonOf(error);
    }
  };
  return <SettingsContext.Provider value={{ theme, save }}>{children}</SettingsContext.Provider>;
}

/** The applied theme, and the way to store new ones; only inside a `SettingsProvider`. */
export function useSettings(): SettingsApi {
  const settings = useContext(SettingsContext);
  if (!settings) throw new Error("useSettings needs a SettingsProvider");
  return settings;
}
