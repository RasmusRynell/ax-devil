import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { WindowBridge } from "./windowBridge";
import { WindowControls } from "./WindowControls";

afterEach(() => {
  cleanup();
  delete window.axDevilWindow;
});

function bridge() {
  let report: (maximized: boolean) => void = () => {};
  const mock = {
    minimize: vi.fn(),
    toggleMaximize: vi.fn(),
    close: vi.fn(),
    onMaximizedChange: vi.fn((listener: (maximized: boolean) => void) => {
      report = listener;
      return () => {};
    }),
  } satisfies WindowBridge;
  window.axDevilWindow = mock;
  return { mock, report: (maximized: boolean) => act(() => report(maximized)) };
}

describe("window controls", () => {
  it("are absent outside the Electron shell", () => {
    render(<WindowControls />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("drive the window through the bridge and follow its maximized state", () => {
    const { mock, report } = bridge();
    render(<WindowControls />);
    fireEvent.click(screen.getByRole("button", { name: "Minimize" }));
    fireEvent.click(screen.getByRole("button", { name: "Maximize" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(mock.minimize).toHaveBeenCalledOnce();
    expect(mock.toggleMaximize).toHaveBeenCalledOnce();
    expect(mock.close).toHaveBeenCalledOnce();
    report(true);
    expect(screen.getByRole("button", { name: "Restore" })).toBeTruthy();
  });
});
