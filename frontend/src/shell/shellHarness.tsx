import { render, screen } from "@testing-library/react";
import { AccessContext, type Access } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import type { SettingsState } from "../generated/api";
import type { CommandId } from "../generated/settings";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import type { SettingsApi } from "../settings";
import { Shell } from "./Shell";

/** The shell under the given settings, access and Session (the window's own by default), once it is seated. */
export async function showShell({
  shortcuts = {},
  values = {},
  access = { owner: true, fullAccess: false },
  file,
  save,
}: {
  shortcuts?: Partial<Record<CommandId, string | null>>;
  values?: Partial<typeof DEFAULT_SETTINGS.values>;
  access?: Access;
  /** Where the backend says its settings and log are, as it tells a caller who may see this machine's files. */
  file?: SettingsState["file"];
  save?: SettingsApi["apply"];
} = {}) {
  const state = {
    ...DEFAULT_SETTINGS,
    file,
    values: { ...DEFAULT_SETTINGS.values, ...values },
    shortcuts: { ...DEFAULT_SETTINGS.shortcuts, ...shortcuts },
  };
  render(
    <AccessContext.Provider value={access}>
      <FakeSettings state={state} apply={save}>
        <SessionProvider>
          <Shell version="1.2.3" />
        </SessionProvider>
      </FakeSettings>
    </AccessContext.Provider>,
  );
  await screen.findByRole("heading", { name: "ax-devil" });
}

/** Shows the shell as `showShell` does and returns the fake room the app joined, for the test to drive. */
export const renderShell = async (options?: Parameters<typeof showShell>[0]) => {
  await showShell(options);
  return FakeSocket.rooms.get("room-1")!;
};
