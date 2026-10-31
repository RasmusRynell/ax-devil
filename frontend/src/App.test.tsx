import { afterEach, beforeAll, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "./App";
import type { Settings } from "./generated/api";

beforeAll(() => {
  // jsdom does not implement modal dialogs.
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.theme;
});

const stored: Settings = {
  "ui.theme": "dark",
  "ui.quick_setup_done": true,
};

/** A backend with the given stored settings; a PUT replaces them, as the real one does. */
function backend(settings: Settings = stored, putStatus = 200) {
  let current = settings;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/info") return { ok: true, json: async () => ({ app: "ax-devil", api: 1 }) };
    if (init?.method === "PUT") {
      if (putStatus !== 200)
        return {
          ok: false,
          status: putStatus,
          json: async () => ({ reason: "no such theme", location: "ui.theme" }),
        };
      current = JSON.parse(init.body as string) as Settings;
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
          ? { ok: true, json: async () => ({ app: "ax-devil", api: 1 }) }
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

  it("stores the Settings dialog choice through the backend and applies it live", async () => {
    const fetchMock = backend();
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    fireEvent.change(await screen.findByLabelText("Theme"), { target: { value: "light" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(put?.[1]?.body as string)).toEqual({
      "ui.theme": "light",
      "ui.quick_setup_done": true,
    });
  });

  it("keeps the dialog open and says why when the backend refuses the settings", async () => {
    backend(stored, 422);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    fireEvent.click(await screen.findByRole("button", { name: "OK" }));
    expect((await screen.findByRole("alert")).textContent).toContain("no such theme");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
