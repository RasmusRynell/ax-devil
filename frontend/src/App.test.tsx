import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "./App";
import { DEFAULT_SETTINGS } from "./fakeSettings";
import type { SettingsChange, SettingsState } from "./generated/api";

beforeAll(() => {
  // jsdom does not implement modal dialogs.
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
});

beforeEach(() => {
  // The workspace's event stream, which this suite leaves silent.
  vi.stubGlobal(
    "EventSource",
    class {
      close() {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.theme;
});

const dark: SettingsState = {
  ...DEFAULT_SETTINGS,
  values: { ...DEFAULT_SETTINGS.values, "ui.theme": "dark" },
};

/** A backend with the given settings; a PUT replaces them, as the real one does, or is refused with `refuse`. */
function backend(settings = dark, refuse?: { reason: string; location: string }) {
  let current = settings;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/info") return { ok: true, json: async () => ({ app: "ax-devil", api: 2 }) };
    if (init?.method === "PUT") {
      if (refuse) return { ok: false, status: 422, json: async () => refuse };
      const change = JSON.parse(init.body as string) as SettingsChange;
      current = { ...current, ...change, restart: ["playback.video_cache"] };
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
          ? { ok: true, json: async () => ({ app: "ax-devil", api: 2 }) }
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

  it("stores the Settings dialog choice through the backend, applies it live and announces a pending restart", async () => {
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
    expect((await screen.findByRole("status")).textContent).toContain("Memory for video caching");
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
        if (url === "/api/info")
          return { ok: true, json: async () => ({ app: "ax-devil", api: 2 }) };
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
