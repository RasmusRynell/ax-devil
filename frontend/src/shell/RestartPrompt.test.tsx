import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GrantsContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import type { ApiError, Grant } from "../generated/api";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { Shell } from "./Shell";
import type { WindowBridge } from "./windowBridge";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  FakeSocket.reset();
  delete window.axDevilWindow;
});

async function showShell(grants: Grant[], save?: () => Promise<ApiError | null>) {
  render(
    <GrantsContext.Provider value={grants}>
      <FakeSettings state={DEFAULT_SETTINGS} save={save}>
        <SessionProvider>
          <Shell version="1.2.3" />
        </SessionProvider>
      </FakeSettings>
    </GrantsContext.Provider>,
  );
  await screen.findByRole("heading", { name: "ax-devil" });
}

/** Saves the Settings dialog with *label* on *page* set to *value*. */
function saveSetting(page: string, label: string, value: string) {
  fireEvent.keyDown(document.body, { key: ",", ctrlKey: true });
  if (page !== "General") fireEvent.click(screen.getByRole("tab", { name: page }));
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "OK" }));
}

describe("restart prompt", () => {
  it("follows a save that changed a setting needing a restart, and offers to restart on the desktop", async () => {
    const restart = vi.fn();
    window.axDevilWindow = {
      restart,
      onMaximizedChange: () => () => undefined,
    } as unknown as WindowBridge;
    await showShell(["configuration", "preferences"]);
    saveSetting("General", "Theme", "dark");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Settings" })).toBeNull());
    expect(screen.queryByRole("dialog", { name: "Restart ax-devil?" })).toBeNull();

    saveSetting("Storage", "Cache folder", "/elsewhere");
    const prompt = await screen.findByRole("dialog", { name: "Restart ax-devil?" });
    expect(prompt.textContent).toContain("Some changes take effect after a restart.");
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    expect(screen.queryByRole("dialog", { name: "Restart ax-devil?" })).toBeNull();
    expect(restart).not.toHaveBeenCalled();

    saveSetting("Storage", "Cache folder", "/elsewhere");
    fireEvent.click(await screen.findByRole("button", { name: "Restart Now" }));
    expect(restart).toHaveBeenCalledOnce();
  });

  it("only says so where the app cannot restart the backend: a browser, or a desktop on one it did not start", async () => {
    window.axDevilWindow = { onMaximizedChange: () => () => undefined } as unknown as WindowBridge;
    await showShell(["configuration"]);
    saveSetting("Storage", "Cache folder", "/elsewhere");
    await screen.findByRole("dialog", { name: "Restart ax-devil?" });
    expect(screen.queryByRole("button", { name: "Restart Now" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(screen.queryByRole("dialog", { name: "Restart ax-devil?" })).toBeNull();
  });

  it("does not follow a refused save, or a save whose dialog was cancelled meanwhile", async () => {
    const refused = { reason: "full", location: "" };
    await showShell(["configuration"], () => Promise.resolve(refused));
    saveSetting("Storage", "Cache folder", "/elsewhere");
    await screen.findByText("full");
    expect(screen.queryByRole("dialog", { name: "Restart ax-devil?" })).toBeNull();
    cleanup();

    let finish: (outcome: ApiError | null) => void = () => undefined;
    await showShell(["configuration"], () => new Promise((resolve) => (finish = resolve)));
    saveSetting("Storage", "Cache folder", "/elsewhere");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    finish(null);
    await new Promise((resolve) => setTimeout(resolve));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
