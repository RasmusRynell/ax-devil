import { AccessContext } from "./client/access";
import type { AppInfo } from "./generated/api";
import { PathPickerProvider } from "./client/pickPath";
import { reasonOf } from "./client/errors";
import { ChangesProvider, refusalOf, useResource } from "./client/resource";
import { SessionProvider } from "./session/session";
import { SettingsProvider } from "./client/settings";
import { SeekProvider } from "./bookmarks/viewerLink";
import { Shell } from "./shell/Shell";

/** The backend's identity and what this window is to it: shown once the backend answers, with the reason when it does not. */
function Backend() {
  // Full access follows `server.password_full_access`, so the info is read again when the settings change.
  const { value: info, error } = useResource<AppInfo>("settings", "/api/info");
  if (info)
    return (
      <AccessContext value={{ owner: info.owner, fullAccess: info.fullAccess }}>
        <SettingsProvider>
          <PathPickerProvider>
            <SessionProvider>
              <SeekProvider>
                <Shell version={info.version} />
              </SeekProvider>
            </SessionProvider>
          </PathPickerProvider>
        </SettingsProvider>
      </AccessContext>
    );
  return (
    <main aria-busy={error === undefined} className="flex h-screen items-center justify-center">
      {error !== undefined && (
        <p role="alert">ax-devil cannot reach its backend: {reasonOf(refusalOf(error))}</p>
      )}
    </main>
  );
}

/** The application window. */
export function App() {
  return (
    <ChangesProvider>
      <Backend />
    </ChangesProvider>
  );
}
