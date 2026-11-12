import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { cleanup, render, screen } from "@testing-library/react";
import { FakeSettings } from "../client/fakeSettings";
import type { PluginsInfo } from "../generated/api";
import { reportFailedPlugins } from "../plugins/failed";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { ActivityBar } from "./ActivityBar";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  FakeSocket.reset();
});

const failedPlugin = (folder: string): PluginsInfo["plugins"][number] => ({
  source: { kind: "installed", manifest: `${folder}/ax-devil-plugin.json`, folder },
  status: { state: "failed", reason: "broken" },
});

it("flags Plugins with how many plugins failed to load, from the window opening and as the view lists them", async () => {
  const info: PluginsInfo = { plugins: [failedPlugin("/p/a"), failedPlugin("/p/b")] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => info })),
  );
  render(
    <FakeSettings>
      <SessionProvider>
        <ActivityBar active={null} onSelect={() => {}} />
      </SessionProvider>
    </FakeSettings>,
  );
  await screen.findByRole("button", { name: "Plugins: 2 failed to load" });
  expect(screen.getByRole("button", { name: "Live" })).toBeTruthy();

  reportFailedPlugins(0);
  await screen.findByRole("button", { name: "Plugins" });
});
