import { browserBacking } from "./client/deviceSettings";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { adoptFragment, credential } from "./client/access";
import { App } from "./App";
import { apiFetch } from "./client/api";
import { deviceStore } from "./client/deviceSettings";
import { DEFAULT_SETTINGS } from "./client/fakeSettings";
import type { ErrorWire, SettingsPatch, SettingsState } from "./generated/api";
import { FakeSocket } from "./session/fakeSession";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  deviceStore.reset("ui.theme");
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  FakeSocket.reset();
  delete document.documentElement.dataset.theme;
});

const info = (access = { owner: true, fullAccess: true }) => ({
  app: "ax-devil",
  api: "2",
  ...access,
});

/** A backend with the given settings; a PATCH changes the keys it names, as the real one does, or is refused with `refuse`. */
function backend(
  settings = DEFAULT_SETTINGS,
  refuse?: ErrorWire,
  access?: { owner: boolean; fullAccess: boolean },
) {
  let current = settings;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/info") return { ok: true, json: async () => info(access) };
    if (init?.method === "PATCH") {
      if (refuse) return { ok: false, status: 422, json: async () => refuse };
      const patch = JSON.parse(init.body as string) as SettingsPatch;
      current = {
        ...current,
        revision: current.revision + 1,
        values: { ...current.values, ...(patch.set as SettingsState["values"]) },
      };
    }
    return { ok: true, json: async () => current };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("app shell", () => {
  it("shows the app, themed from this device's settings", async () => {
    backend();
    deviceStore.set("ui.theme", "dark");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "ax-devil" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("says why instead of showing an empty window when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")));
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("connection refused");
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("says why when the stored settings cannot be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/info"
          ? { ok: true, json: async () => info() }
          : {
              ok: false,
              status: 500,
              json: async () => ({
                code: "Internal",
                params: {},
                reason: "settings file unreadable",
              }),
            },
      ),
    );
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("settings file unreadable");
  });

  it("sends a Settings dialog choice of the server to it, and keeps the theme on the device", async () => {
    const fetchMock = backend(DEFAULT_SETTINGS, undefined, { owner: true, fullAccess: false });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    fireEvent.change(await screen.findByLabelText("Theme"), { target: { value: "light" } });
    fireEvent.click(screen.getByRole("tab", { name: "Storage" }));
    fireEvent.change(screen.getByLabelText("Cache folder"), { target: { value: "/var/cache" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(patch?.[1]?.body as string)).toEqual({
      set: { "storage.cache_dir": "/var/cache" },
    });
    expect(browserBacking().read()).toEqual({
      "ui.theme": "light",
    });
  });

  it("keeps the dialog open, says why at the value the backend refused and changes nothing on the device", async () => {
    backend(DEFAULT_SETTINGS, {
      code: "InvalidRequest",
      params: {},
      reason: "no such folder",
      location: "storage.cache_dir",
    });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    fireEvent.change(await screen.findByLabelText("Theme"), { target: { value: "light" } });
    fireEvent.click(screen.getByRole("tab", { name: "Storage" }));
    fireEvent.change(screen.getByLabelText("Cache folder"), { target: { value: "/nowhere" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("no such folder");
    expect(within(alert.parentElement as HTMLElement).getByLabelText("Cache folder")).toBeTruthy();
    expect(window.localStorage.getItem("ax-devil.device")).toBeNull();
  });

  it("ignores a slow save that finishes after its dialog was closed, and never saves twice", async () => {
    let finish: () => void = () => {};
    const puts: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === "/api/info") return { ok: true, json: async () => info() };
        if (init?.method !== "PATCH") return { ok: true, json: async () => DEFAULT_SETTINGS };
        puts.push(init.body as string);
        await new Promise<void>((resolve) => {
          finish = resolve;
        });
        const patch = JSON.parse(init.body as string) as SettingsPatch;
        const values = { ...DEFAULT_SETTINGS.values, ...patch.set };
        return { ok: true, json: async () => ({ ...DEFAULT_SETTINGS, revision: 1, values }) };
      }),
    );
    render(<App />);
    const folder = async (value: string) => {
      fireEvent.click(await screen.findByRole("tab", { name: "Storage" }));
      fireEvent.change(screen.getByLabelText("Cache folder"), { target: { value } });
    };
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    await folder("/one");
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect((screen.getByRole("button", { name: "OK" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(screen.getByRole("dialog", { hidden: true }).querySelector("form") as Element);
    expect(puts).toHaveLength(1);

    fireEvent(
      screen.getByRole("dialog", { hidden: true }),
      new Event("cancel", { cancelable: true }),
    );
    expect(screen.queryByRole("dialog", { hidden: true })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    await folder("/two");
    finish();
    await waitFor(() => expect(puts).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("dialog", { hidden: true })).toBeTruthy();
    expect((screen.getByLabelText("Cache folder") as HTMLInputElement).value).toBe("/two");
  });
});

describe("access", () => {
  it("moves a link or password out of the address bar and sends the password as a bearer", async () => {
    window.history.replaceState(null, "", "/?tab=1#join=ann.s3cret");
    adoptFragment();
    expect(window.location.hash).toBe("");
    expect(window.location.search).toBe("?tab=1");
    expect(credential()).toEqual({ kind: "join", session: "ann", secret: "s3cret" });

    window.history.replaceState(null, "", "/#password=p%40ss");
    adoptFragment();
    expect(window.location.href).not.toContain("p%40ss");
    expect(credential()).toEqual({ kind: "password", password: "p@ss" });
    const fetchMock = backend();
    await apiFetch("/api/sessions");
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer p@ss");
  });

  it("treats a password the header cannot carry as a rejected one, without failing to start", async () => {
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "password", password: "秘密" }),
    );
    FakeSocket.serverPassword = "secret";
    const fetchMock = backend();
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("not valid");
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toBeUndefined();
  });

  it("gives a page that is not the Owner only the Device settings, kept on the device and not on the server", async () => {
    const fetchMock = vi.fn(async (url: string) => ({
      ok: url === "/api/info",
      status: 401,
      json: async () => info({ owner: false, fullAccess: false }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    window.localStorage.setItem("ax-devil.preferences", JSON.stringify({ values: {} }));
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    fireEvent.change(await screen.findByLabelText("Theme"), { target: { value: "light" } });
    expect(screen.queryByLabelText("Show lane names")).toBeNull();
    expect(screen.queryByRole("button", { name: "Keyboard shortcuts" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(window.localStorage.getItem("ax-devil.preferences")).toBeNull();
    expect(fetchMock.mock.calls.every(([url]) => url !== "/api/settings")).toBe(true);
  });
});

describe("settings changed elsewhere", () => {
  it("are read again with the info, only for a notice above the revision held", async () => {
    let held: SettingsState = { ...DEFAULT_SETTINGS, revision: 3 };
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      json: async () => (url === "/api/info" ? info() : held),
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByRole("heading", { name: "ax-devil" });
    const reads = () =>
      fetchMock.mock.calls
        .map(([url]) => url)
        .filter((url) => url === "/api/info" || url === "/api/settings")
        .sort();
    expect(reads()).toEqual(["/api/info", "/api/settings"]);

    // Another window changed the folder; this window learns it from the notice and from nothing else.
    const folder = async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
      fireEvent.click(await screen.findByRole("tab", { name: "Storage" }));
      const value = (screen.getByLabelText("Cache folder") as HTMLInputElement).value;
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      return value;
    };
    held = { ...held, revision: 4, values: { ...held.values, "storage.cache_dir": "/other" } };
    await act(async () =>
      FakeSocket.latest.emit({ type: "changed", topic: "settings", revision: 4 }),
    );
    await waitFor(async () => expect(await folder()).toBe("/other"));
    expect(reads()).toEqual(["/api/info", "/api/info", "/api/settings", "/api/settings"]);

    held = { ...held, revision: 3, values: { ...held.values, "storage.cache_dir": "/older" } };
    await act(async () =>
      FakeSocket.latest.emit({ type: "changed", topic: "settings", revision: 4 }),
    );
    expect(reads()).toHaveLength(4);
    expect(await folder()).toBe("/other");
  });
});
