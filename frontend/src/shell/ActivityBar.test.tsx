import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { cleanup, render, screen } from "@testing-library/react";
import { FakeSettings } from "../client/fakeSettings";
import type { PluginsInfo } from "../generated/api";
import { AccessContext } from "../client/access";
import { PluginViewsProvider } from "../plugins/listing";
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

it("flags Plugins with how many plugins failed to load", async () => {
  const info: PluginsInfo = { plugins: [failedPlugin("/p/a"), failedPlugin("/p/b")] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => info })),
  );
  render(
    <AccessContext value={{ owner: true, fullAccess: true }}>
      <FakeSettings>
        <SessionProvider>
          <PluginViewsProvider>
            <ActivityBar active={null} onSelect={() => {}} />
          </PluginViewsProvider>
        </SessionProvider>
      </FakeSettings>
    </AccessContext>,
  );
  await screen.findByRole("button", { name: "Plugins: 2 failed to load" });
  expect(screen.getByRole("button", { name: "Live" })).toBeTruthy();
});
