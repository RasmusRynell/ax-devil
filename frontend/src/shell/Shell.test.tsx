import { afterEach, describe, expect, it } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MENUS } from "../commands";
import { Shell } from "./Shell";

afterEach(cleanup);

describe("menu bar", () => {
  it("lists the menus in order, with each shortcut beside its item", () => {
    render(<Shell />);
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(
      MENUS.map((menu) => menu.label),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    expect(screen.getByRole("menuitem", { name: /Save Workspace As/ }).textContent).toContain(
      "Ctrl+Shift+S",
    );
    fireEvent.click(screen.getByRole("menuitem", { name: /Open Recent/ }));
    expect(screen.getByRole("menuitem", { name: "None" }).hasAttribute("disabled")).toBe(true);
  });

  it("opens with the keyboard and closes with Escape", () => {
    render(<Shell />);
    const view = screen.getByRole("menuitem", { name: "View" });
    view.focus();
    fireEvent.keyDown(view, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toContain("Toggle Fullscreen");
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(view);
  });

  it("closes when focus leaves the bar, so Tab and clicks into the app do not leave it hanging open", () => {
    render(<Shell />);
    const file = screen.getByRole("menuitem", { name: "File" });
    file.focus();
    fireEvent.click(file);
    expect(screen.getByRole("menu")).toBeTruthy();
    // Only the focused menu button is a Tab stop.
    expect(screen.getByRole("menuitem", { name: "View" }).getAttribute("tabindex")).toBe("-1");
    act(() => screen.getByRole("button", { name: "Live" }).focus());
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("toggles a checkable item", () => {
    render(<Shell />);
    fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Show Lane Names" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
    expect(
      screen
        .getByRole("menuitemcheckbox", { name: "Show Lane Names" })
        .getAttribute("aria-checked"),
    ).toBe("true");
  });
});

describe("sidebar", () => {
  it("hides from the View menu and from the shortcut, and from the active view's button", () => {
    render(<Shell />);
    expect(screen.getByRole("complementary", { name: "Sidebar" })).toBeTruthy();
    fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Sidebar/ }));
    expect(screen.queryByRole("complementary")).toBeNull();
    fireEvent.keyDown(document, { key: "\\", ctrlKey: true });
    expect(screen.getByRole("complementary")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Workspace" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Live" }));
    expect(screen.getByRole("heading", { name: "Live" })).toBeTruthy();
  });
});
