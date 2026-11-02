import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BrowseButton, MenuButton } from "./Button";

afterEach(cleanup);

describe("menu button", () => {
  it("opens its menu at once, hands over the chosen command and closes", () => {
    const onSelect = vi.fn();
    render(
      <MenuButton
        label="Export"
        entries={[{ kind: "action", id: "csv", label: "As CSV" }]}
        onSelect={onSelect}
      />,
    );
    const button = screen.getByRole("button", { name: "Export" });
    expect(button.getAttribute("aria-haspopup")).toBe("menu");
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("menuitem", { name: "As CSV" }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "csv" }));
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(button);
  });
});

describe("browse button", () => {
  it("is named for what it chooses", () => {
    const onBrowse = vi.fn();
    render(<BrowseButton what="cache folder" onBrowse={onBrowse} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose cache folder" }));
    expect(onBrowse).toHaveBeenCalledTimes(1);
  });
});
