import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { UncaughtErrors } from "./UncaughtErrors";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.axDevilWindow;
});

const throwUncaught = (error: unknown) =>
  act(() => void window.dispatchEvent(new ErrorEvent("error", { error })));

describe("uncaught errors", () => {
  it("shows each error in turn with its stack trace, and Continue goes on to the next", () => {
    render(<UncaughtErrors />);
    expect(screen.queryByRole("dialog")).toBeNull();
    throwUncaught(new TypeError("first failure"));
    const rejection = new Event("unhandledrejection");
    Object.assign(rejection, { reason: new RangeError("second failure") });
    act(() => void window.dispatchEvent(rejection));

    const dialog = screen.getByRole("dialog", { name: "Application Error" });
    expect(dialog.textContent).toContain("TypeError: first failure");
    expect(dialog.querySelector("pre")?.textContent).toContain("TypeError: first failure");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("dialog").textContent).toContain("RangeError: second failure");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows something for a reason that is not an Error, even one JSON cannot print", () => {
    render(<UncaughtErrors />);
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    for (const reason of [undefined, circular, 10n]) {
      const rejection = new Event("unhandledrejection");
      Object.assign(rejection, { reason });
      act(() => void window.dispatchEvent(rejection));
    }
    for (const shown of ["undefined", "[object Object]", "10"]) {
      expect(screen.getByRole("dialog").textContent).toContain(`occurred:${shown}`);
      fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    }
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("tells the backend each error with the page's address but not its fragment, and offers Reload in a browser", () => {
    const fetched = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetched);
    window.history.replaceState(null, "", "/session#password=secret");
    render(<UncaughtErrors />);
    throwUncaught(new TypeError("reported"));
    const [path, init] = fetched.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/api/client-errors");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toMatchObject({
      summary: "TypeError: reported",
      page: `${window.location.origin}/session`,
      desktop: false,
    });
    expect(JSON.stringify(body)).not.toContain("secret");
    expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy();
    window.history.replaceState(null, "", "/");
  });

  it("ignores the browser's own notices that carry no error", () => {
    render(<UncaughtErrors />);
    act(
      () => void window.dispatchEvent(new ErrorEvent("error", { message: "ResizeObserver loop" })),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("offers Close only where a desktop window can close, and Escape continues rather than closes", () => {
    render(<UncaughtErrors />);
    throwUncaught(new Error("boom"));
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
    cleanup();

    let closed = 0;
    window.axDevilWindow = { close: () => closed++ } as unknown as Window["axDevilWindow"];
    render(<UncaughtErrors />);
    throwUncaught(new Error("boom"));
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(closed).toBe(0);

    throwUncaught(new Error("again"));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(closed).toBe(1);
  });
});
