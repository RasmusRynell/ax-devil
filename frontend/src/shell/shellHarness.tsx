import { render, screen } from "@testing-library/react";
import { GrantsContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import type { Grant } from "../generated/api";
import type { CommandId } from "../generated/settings";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import type { SettingsApi } from "../settings";
import { Shell } from "./Shell";

/** The shell under the given settings, grants and Session (the window's own by default), once it is seated. */
export async function showShell({
  shortcuts = {},
  values = {},
  grants = ["configuration"],
  save,
}: {
  shortcuts?: Partial<Record<CommandId, string | null>>;
  values?: Partial<typeof DEFAULT_SETTINGS.values>;
  grants?: Grant[];
  save?: SettingsApi["save"];
} = {}) {
  const state = {
    ...DEFAULT_SETTINGS,
    values: { ...DEFAULT_SETTINGS.values, ...values },
    shortcuts: { ...DEFAULT_SETTINGS.shortcuts, ...shortcuts },
  };
  render(
    <GrantsContext.Provider value={grants}>
      <FakeSettings state={state} save={save}>
        <SessionProvider>
          <Shell version="1.2.3" />
        </SessionProvider>
      </FakeSettings>
    </GrantsContext.Provider>,
  );
  await screen.findByRole("heading", { name: "ax-devil" });
}

/** Shows the shell as `showShell` does and returns the fake room the app joined, for the test to drive. */
export const renderShell = async (options?: Parameters<typeof showShell>[0]) => {
  await showShell(options);
  return FakeSocket.rooms.get("room-1")!;
};
