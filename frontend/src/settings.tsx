import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import type { ApiError, Settings } from "./generated/api";

export const THEMES = ["auto", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

interface SettingsApi {
  theme: Theme;
  /** Stores *next* through the backend and shows what it returns; resolves to the refusal reason, else null. */
  save: (next: Theme) => Promise<string | null>;
}

type Load =
  | { state: "loading" }
  | { state: "ready"; stored: Settings }
  | { state: "failed"; reason: string };

const SettingsContext = createContext<SettingsApi | null>(null);

async function request(init?: RequestInit): Promise<Settings> {
  const response = await fetch("/api/settings", init);
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(error?.reason ?? `the backend answered ${response.status}`);
  }
  return (await response.json()) as Settings;
}

const reasonOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

/**
 * The backend's stored settings are the only copy. Children render once they arrive, with the theme
 * applied live to the document root; a failed read shows its reason instead.
 */
export function SettingsProvider({ children }: { children: ReactNode }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  useEffect(() => {
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
  }, []);
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
      const replaced = await request({
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...stored, "ui.theme": next }),
      });
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
