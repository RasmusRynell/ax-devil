import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, render, screen } from "@testing-library/react";
import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("app shell", () => {
  it("shows the app once the backend answers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ app: "ax-devil", api: 1 }) }),
    );
    render(<App />);
    expect(await screen.findByRole("heading", { name: "ax-devil" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says why instead of showing an empty window when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")));
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("connection refused");
    expect(screen.queryByRole("heading")).toBeNull();
  });
});
