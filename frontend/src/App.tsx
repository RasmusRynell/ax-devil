import { useEffect, useState } from "react";
import { GrantsContext } from "./access";
import { apiFetch } from "./api";
import type { AppInfo } from "./generated/api";
import { SessionProvider } from "./session/session";
import { SettingsProvider } from "./settings";
import { Shell } from "./shell/Shell";

type Backend =
  | { state: "connecting" }
  | { state: "ready"; info: AppInfo }
  | { state: "failed"; reason: string };

/** The application window: shown once the backend answers, with the reason when it does not. */
export function App() {
  const [backend, setBackend] = useState<Backend>({ state: "connecting" });
  useEffect(() => {
    const request = new AbortController();
    apiFetch("/api/info", { signal: request.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`the backend answered ${response.status}`);
        setBackend({ state: "ready", info: (await response.json()) as AppInfo });
      })
      .catch((error: unknown) => {
        if (!request.signal.aborted)
          setBackend({
            state: "failed",
            reason: error instanceof Error ? error.message : String(error),
          });
      });
    return () => request.abort();
  }, []);
  if (backend.state === "ready")
    return (
      <GrantsContext value={backend.info.grants}>
        <SettingsProvider>
          <SessionProvider>
            <Shell version={backend.info.version} />
          </SessionProvider>
        </SettingsProvider>
      </GrantsContext>
    );
  return (
    <main
      aria-busy={backend.state === "connecting"}
      className="flex h-screen items-center justify-center"
    >
      {backend.state === "failed" && (
        <p role="alert">ax-devil cannot reach its backend: {backend.reason}</p>
      )}
    </main>
  );
}
