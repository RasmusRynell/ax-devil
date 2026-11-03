import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { GrantsContext } from "../access";
import type { PluginInfo, PluginsInfo } from "../generated/api";
import { PluginsView } from "./PluginsView";

const MOT: PluginInfo = {
  source: { kind: "built_in" },
  status: {
    state: "loaded",
    id: "mot",
    name: "mot",
    version: "0.1.0",
    handlers: [{ kind: "decoder", id: "MOT_FILE", name: "MOT", extensions: [".txt"] }],
  },
};
const ACME: PluginInfo = {
  source: { kind: "installed", manifest: "/p/acme/ax-devil-plugin.json", folder: "/p/acme" },
  status: {
    state: "loaded",
    id: "acme",
    name: "Acme",
    version: "1.4.0",
    handlers: [
      { kind: "decoder", id: "acme_tracks", name: "Acme tracks", extensions: [".acme.jsonl"] },
      { kind: "playlist_resolver", id: "acme_runs", name: "Acme runs", extensions: [] },
    ],
  },
};
const BROKEN: PluginInfo = {
  source: { kind: "installed", manifest: "/p/broken/ax-devil-plugin.json", folder: "/p/broken" },
  status: { state: "failed", reason: "handlers[0].id: 'MOT_FILE' is already taken" },
};

/** A backend whose installed plugins `remove` takes away. */
function backend(plugins: PluginInfo[]) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/plugins/remove") {
        const { target } = JSON.parse(init?.body as string) as { target: string };
        plugins = plugins.filter(
          (plugin) => plugin.source.kind !== "installed" || plugin.source.manifest !== target,
        );
      }
      const info: PluginsInfo = { plugins };
      return { ok: true, json: async () => info };
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const view = () =>
  render(
    <GrantsContext value={["preferences", "files"]}>
      <PluginsView />
    </GrantsContext>,
  );

describe("plugins view", () => {
  it("lists failed, installed and built-in plugins, says why one failed, removes one, and installs only a typed folder", async () => {
    const calls = backend([MOT, ACME, BROKEN]);
    view();

    const failed = await screen.findByRole("region", { name: "Failed" });
    expect(within(failed).getByRole("alert").textContent).toContain("'MOT_FILE' is already taken");
    const installed = screen.getByRole("region", { name: "Installed" });
    expect(within(installed).getByText("Acme")).toBeTruthy();
    expect(within(installed).getByText("Acme tracks")).toBeTruthy();
    expect(within(installed).getByText(".acme.jsonl")).toBeTruthy();
    expect(within(installed).getByText("Acme runs")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Built-in" })).getByText("MOT")).toBeTruthy();
    expect(
      within(screen.getByRole("region", { name: "Built-in" })).queryByRole("button"),
    ).toBeNull();

    fireEvent.click(within(failed).getByRole("button", { name: "Remove broken" }));
    await vi.waitFor(() => expect(screen.queryByRole("region", { name: "Failed" })).toBeNull());
    expect(calls).toContain("POST /api/plugins/remove");
    expect(screen.getByRole("region", { name: "Installed" })).toBeTruthy();

    // Installing needs a folder.
    const install = screen.getByRole("button", { name: "Install" }) as HTMLButtonElement;
    expect(install.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Folder"), { target: { value: "/p/new" } });
    expect(install.disabled).toBe(false);
  });
});
