import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { adoptFragment, credential } from "./access";
import { App } from "./App";
import { apiFetch } from "./api";
import { DEFAULT_SETTINGS } from "./fakeSettings";
import type { SettingsChange, SettingsState } from "./generated/api";
import { FakeSocket } from "./session/fakeSession";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  FakeSocket.reset();
  delete document.documentElement.dataset.theme;
});

const dark: SettingsState = {
  ...DEFAULT_SETTINGS,
  values: { ...DEFAULT_SETTINGS.values, "ui.theme": "dark" },
};

const info = (grants = ["preferences", "configuration", "sessions"]) => ({
  app: "ax-devil",
  api: 2,
  grants,
});

/** A backend with the given settings; a PUT replaces them, as the real one does, or is refused with `refuse`. */
function backend(
  settings = dark,
  refuse?: { reason: string; location: string },
  grants?: string[],
) {
  let current = settings;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/info") return { ok: true, json: async () => info(grants) };
    if (init?.method === "PUT") {
      if (refuse) return { ok: false, status: 422, json: async () => refuse };
      const change = JSON.parse(init.body as string) as SettingsChange;
      current = { ...current, ...change };
    }
    return { ok: true, json: async () => current };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("app shell", () => {
  it("shows the app, themed from the stored settings", async () => {
    backend();
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
              json: async () => ({ reason: "settings file unreadable", location: "" }),
            },
      ),
    );
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("settings file unreadable");
  });

  it("stores the Settings dialog choice through the backend, and applies it live", async () => {
    const fetchMock = backend();
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    fireEvent.change(await screen.findByLabelText("Theme"), { target: { value: "light" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(put?.[1]?.body as string)).toEqual({
      values: { ...dark.values, "ui.theme": "light" },
      shortcuts: dark.shortcuts,
    });
  });

  it("keeps the dialog open and says why at the value the backend refused", async () => {
    backend(dark, { reason: "no such theme", location: "ui.theme" });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    fireEvent.click(await screen.findByRole("button", { name: "OK" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("no such theme");
    expect(within(alert.parentElement as HTMLElement).getByLabelText("Theme")).toBeTruthy();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("ignores a slow save that finishes after its dialog was closed, and never saves twice", async () => {
    let finish: () => void = () => {};
    const puts: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === "/api/info") return { ok: true, json: async () => info() };
        if (init?.method !== "PUT") return { ok: true, json: async () => dark };
        puts.push(init.body as string);
        await new Promise<void>((resolve) => {
          finish = resolve;
        });
        const change = JSON.parse(init.body as string) as SettingsChange;
        return { ok: true, json: async () => ({ ...dark, ...change }) };
      }),
    );
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    fireEvent.change(await screen.findByLabelText("Theme"), { target: { value: "light" } });
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
    fireEvent.change(await screen.findByLabelText("Theme"), { target: { value: "auto" } });
    finish();
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    expect(screen.getByRole("dialog", { hidden: true })).toBeTruthy();
    expect((screen.getByLabelText("Theme") as HTMLSelectElement).value).toBe("auto");
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

  it("keeps the theme and shortcuts of a viewer without the preferences grant in the browser, not on the server", async () => {
    const fetchMock = vi.fn(async (url: string) => ({
      ok: url === "/api/info",
      status: 401,
      json: async () => info(["sessions"]),
    }));
    vi.stubGlobal("fetch", fetchMock);
    window.localStorage.setItem(
      "ax-devil.preferences",
      JSON.stringify({ values: { "ui.theme": "dark" } }),
    );
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent.change(await screen.findByLabelText("Theme"), { target: { value: "light" } });
    expect(screen.queryByLabelText("Cache folder")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(JSON.parse(window.localStorage.getItem("ax-devil.preferences") ?? "{}")).toMatchObject({
      values: { "ui.theme": "light" },
      shortcuts: {},
    });
    expect(fetchMock.mock.calls.every(([url]) => url !== "/api/settings")).toBe(true);
  });

  it("sends a server-password holder's Server settings to the server and keeps the theme in the browser", async () => {
    const fetchMock = backend(DEFAULT_SETTINGS, undefined, ["configuration", "sessions"]);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    fireEvent.change(await screen.findByLabelText("Theme"), { target: { value: "light" } });
    fireEvent.click(screen.getByRole("tab", { name: "Storage" }));
    fireEvent.change(screen.getByLabelText("Cache folder"), { target: { value: "/var/cache" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    const sent = JSON.parse(put?.[1]?.body as string) as SettingsChange;
    expect(sent.values).toEqual({
      "playback.video_cache": "auto",
      "storage.cache_dir": "/var/cache",
      "storage.catalog_dir": "",
    });
    expect(JSON.parse(window.localStorage.getItem("ax-devil.preferences") ?? "{}")).toMatchObject({
      values: { "ui.theme": "light" },
    });
  });
});
