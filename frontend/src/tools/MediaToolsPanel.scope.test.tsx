import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { READY, file, ids, mount, search, showFile } from "./panelRig";

beforeEach(() => vi.stubGlobal("ResizeObserver", undefined));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Objects scopes", () => {
  it("counts the matches against the frame's objects before the lane's filter hides any", () => {
    mount({}, { keep: "score > 0.7" });
    expect(ids()).toEqual(["12", "7"]);
    expect(screen.getByText("2 of 3")).toBeTruthy();
    search("class:car");
    expect(screen.getByText("1 of 3")).toBeTruthy();
  });

  it("describes both scopes on hover", () => {
    mount();
    const hint = screen
      .getByRole("radio", { name: "File" })
      .closest("[title]")
      ?.getAttribute("title");
    expect(hint).toContain("Objects in the displayed frame, searchable by any attribute");
    expect(hint).toContain(
      "Every object in the file; objects not in the displayed frame are dimmed",
    );
  });

  it("drops an Enter awaiting an unloaded row when the file's list is replaced, rather than open what lands there", () => {
    const { answer, reread, selected } = mount();
    showFile();
    const rows = Array.from({ length: 40 }, (_, i) => file(`o${i}`, i));
    answer(rows.slice(0, 5), { total: 40, matched: 40 });
    const list = screen.getByRole("listbox", { name: "Objects" });
    fireEvent.keyDown(list, { key: "End" });
    fireEvent.keyDown(list, { key: "Enter" });
    reread(2);
    answer(rows, { total: 40, matched: 40, data_revision: 2 });
    expect(selected).toEqual([]);
  });

  it("keeps File scope while the lane's file is not read, such as after a reconnect, and returns to it", () => {
    const { status, answer } = mount();
    showFile();
    answer([file("7", 3)]);
    const filed = () => (screen.getByRole("radio", { name: "File" }) as HTMLInputElement).checked;
    expect(filed()).toBe(true);

    status({ status: "Loading" });
    expect(screen.queryByRole("radio", { name: "File" })).toBeNull();
    expect(ids()).toEqual(["12", "7", "9"]);

    status(READY);
    expect(filed()).toBe(true);
  });
});

it("keeps the Objects scroll and cursor across Events and back", () => {
  mount();
  const list = screen.getByRole("listbox", { name: "Objects" });
  fireEvent.keyDown(list, { key: "ArrowDown" });
  const cursorText = (table: HTMLElement) =>
    document.getElementById(table.getAttribute("aria-activedescendant") ?? "")?.textContent;
  const cursor = cursorText(list);
  fireEvent.scroll(list.closest<HTMLElement>(".overflow-y-auto")!, { target: { scrollTop: 30 } });
  fireEvent.click(screen.getByRole("tab", { name: /Events/ }));
  fireEvent.click(screen.getByRole("tab", { name: "Objects" }));
  const returned = screen.getByRole("listbox", { name: "Objects" });
  expect(returned.closest<HTMLElement>(".overflow-y-auto")!.scrollTop).toBe(30);
  expect(cursor).toBeTruthy();
  expect(cursorText(returned)).toBe(cursor);
});
