import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AccessContext } from "../access";
import type { PluginInfo, PluginsInfo } from "../generated/api";
import { PluginsView } from "./PluginsView";

const MOT: PluginInfo = {
  source: { kind: "built_in" },
  status: {
    state: "loaded",
    id: "mot",
    name: "mot",
    version: "0.1.0",
    skipped: [],
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
    skipped: [
      { kind: "encoder", id: "acme_out", reason: "this version has no encoders" },
      { kind: "gadget", reason: "unknown kind" },
    ],
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

/** A backend whose installed plugins `remove` takes away; `update` shows a plugin's version as 2.0.0 and refuses a failed one. */
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
      if (url === "/api/plugins/update") {
        const { target } = JSON.parse(init?.body as string) as { target: string };
        const plugin = plugins.find(
          (entry) => entry.source.kind === "installed" && entry.source.manifest === target,
        );
        if (plugin?.status.state === "failed") {
          return {
            ok: false,
            status: 400,
            json: async () => ({ code: "PluginInvalid", params: {}, reason: "not valid JSON" }),
          };
        }
        plugins = plugins.map((entry) =>
          entry === plugin && entry.status.state === "loaded"
            ? { ...entry, status: { ...entry.status, version: "2.0.0" } }
            : entry,
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
    <AccessContext value={{ owner: true, fullAccess: true }}>
      <PluginsView />
    </AccessContext>,
  );

describe("plugins view", () => {
  it("lists failed, installed and built-in plugins, says why one failed, shows what it skipped, updates and removes, and installs only a typed folder", async () => {
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

    // What a plugin lists but the app does not use is said, one line each.
    expect(
      within(installed).getByText("encoder acme_out: this version has no encoders"),
    ).toBeTruthy();
    expect(within(installed).getByText("gadget -: unknown kind")).toBeTruthy();

    // Update reads the manifest again: a good one shows its new version, a broken one says why.
    expect(within(installed).getByText("1.4.0")).toBeTruthy();
    fireEvent.click(within(installed).getByRole("button", { name: "Update Acme" }));
    await within(installed).findByText("2.0.0");
    expect(calls).toContain("POST /api/plugins/update");
    fireEvent.click(within(failed).getByRole("button", { name: "Update broken" }));
    await screen.findByText("not valid JSON");
    expect(within(failed).getByRole("button", { name: "Remove broken" })).toBeTruthy();

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

  it("keeps a refused install visible after the list reloads, and recovers when the request fails outright", async () => {
    let outcome: "refused" | "unreachable" = "refused";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method !== "POST") return { ok: true, json: async () => ({ plugins: [MOT] }) };
        if (outcome === "unreachable") throw new TypeError("backend restarting");
        return {
          ok: false,
          status: 400,
          json: async () => ({
            code: "PluginInvalid",
            params: {},
            reason: "no manifest in /p/new",
          }),
        };
      }),
    );
    view();
    await screen.findByRole("region", { name: "Built-in" });
    const install = screen.getByRole("button", { name: "Install" }) as HTMLButtonElement;
    const folder = screen.getByLabelText("Folder");

    fireEvent.change(folder, { target: { value: "/p/new" } });
    fireEvent.click(install);
    await screen.findByText("no manifest in /p/new");
    window.dispatchEvent(new Event("focus")); // a list reload answers after the refusal
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText("no manifest in /p/new")).toBeTruthy();

    outcome = "unreachable";
    fireEvent.click(install);
    await screen.findByText("backend restarting");
    expect(install.disabled).toBe(false);
  });

  it("keeps an installed plugin listed when an older list fetch answers after the install", async () => {
    let answerOldList = (_info: PluginsInfo) => {};
    let first = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method === "POST")
          return { ok: true, json: async () => ({ plugins: [MOT, ACME] }) };
        if (!first) return { ok: true, json: async () => ({ plugins: [MOT] }) };
        first = false;
        const old = await new Promise<PluginsInfo>((resolve) => (answerOldList = resolve));
        return { ok: true, json: async () => old };
      }),
    );
    view();
    fireEvent.change(screen.getByLabelText("Folder"), { target: { value: "/p/acme" } });
    fireEvent.click(screen.getByRole("button", { name: "Install" }));
    await screen.findByRole("region", { name: "Installed" });

    answerOldList({ plugins: [] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("region", { name: "Installed" })).toBeTruthy();
    expect(screen.getByText("Acme")).toBeTruthy();
  });
});
