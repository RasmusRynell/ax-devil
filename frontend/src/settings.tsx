import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import type { ApiError, SettingsChange, SettingsState } from "./generated/api";

export interface SettingsApi extends SettingsState {
  /**
   * Stores *change* through the backend and shows what it returns; resolves to the refusal (its `location` is a
   * setting key or `shortcuts.<id>`, empty when the backend could not be reached), else null.
   */
  save: (change: SettingsChange) => Promise<ApiError | null>;
}

type Load =
  | { state: "loading" }
  | { state: "ready"; stored: SettingsState }
  | { state: "failed"; reason: string };

/** Provided by `SettingsProvider`; tests provide their own value. */
export const SettingsContext = createContext<SettingsApi | null>(null);

async function request(init?: RequestInit): Promise<SettingsState> {
  const response = await fetch("/api/settings", init);
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
        if (current) setLoad({ state: "failed", reason: refusalOf(error).reason });
      });
    return () => {
      current = false;
    };
  }, []);
  const stored = load.state === "ready" ? load.stored : null;
  const theme = stored?.values["ui.theme"] as string | undefined;
  useLayoutEffect(() => {
    // tokens.css selects the color scheme from this attribute.
    if (theme) document.documentElement.dataset.theme = theme;
  }, [theme]);

  if (load.state === "failed")
    return <p role="alert">ax-devil cannot read its settings: {load.reason}</p>;
  if (!stored) return <main aria-busy="true" />;

  const save = async (change: SettingsChange) => {
    try {
      const replaced = await request({
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(change),
      });
      setLoad({ state: "ready", stored: replaced });
      return null;
    } catch (error) {
      return refusalOf(error);
    }
  };
  return (
    <SettingsContext.Provider value={{ ...stored, save }}>{children}</SettingsContext.Provider>
  );
}

/** The stored settings, the effective shortcuts, and the way to store new ones; only inside a `SettingsProvider`. */
export function useSettings(): SettingsApi {
  const settings = useContext(SettingsContext);
  if (!settings) throw new Error("useSettings needs a SettingsProvider");
  return settings;
}
