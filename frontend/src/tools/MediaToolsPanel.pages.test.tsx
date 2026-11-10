import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { fullSample } from "../present/fixtures";
import { EMPTY, push } from "./pageStack";
import { file, FRAME, ids, mount, search, showFile } from "./panelRig";

beforeEach(() => vi.stubGlobal("ResizeObserver", undefined));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Object pages", () => {
  const page = () => screen.getByRole("region", { name: "Object page" });
  const title = () => within(page()).getByRole("heading").textContent;
  const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
  const edges = () => [button("Previous in list").disabled, button("Next in list").disabled];
  const marked = () =>
    screen
      .getAllByRole("option")
      .filter((row) => row.getAttribute("aria-selected") === "true")
      .map((row) => row.firstElementChild?.textContent);

  it("opens from a row, and each way back returns one level to the list as it was left", () => {
    const { answer, selected, outside } = mount();
    showFile();
    search("class:car");
    answer([file("7", 3), file("9", 8), file("40", 55)]);
    const panel = screen.getByRole("complementary", { name: "Media tools" });
    const ways = [
      () => fireEvent.click(button("Back to Objects")),
      () => fireEvent.keyDown(page(), { key: "Escape" }),
      () => fireEvent.keyDown(page(), { key: "Backspace" }),
      () => fireEvent.mouseDown(panel, { button: 3 }),
    ];
    for (const back of ways) {
      fireEvent.click(screen.getAllByRole("option")[1]);
      expect(title()).toBe("9");
      back();
      expect(screen.queryByRole("region", { name: "Object page" })).toBeNull();
      expect(ids()).toEqual(["7", "9", "40"]);
      expect(screen.getByRole("radio", { name: "File" })).toHaveProperty("checked", true);
      expect(screen.getByRole("textbox", { name: "Search objects" })).toHaveProperty(
        "value",
        "class:car",
      );
      expect(marked()).toEqual(["9"]);
    }
    expect(selected).toEqual(["9", null, "9", null, "9", null, "9", null]);
    // The keys that went back were spent; with no page open the same Escape is the viewer's.
    expect(outside).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(outside).toHaveBeenCalledOnce();
    // The release of the mouse's Back button is not left to the browser.
    expect(fireEvent.mouseUp(panel, { button: 3 })).toBe(false);
  });

  it("steps through the list as it is now without moving the video, and not from an object it lost", () => {
    const { source, selected, play } = mount();
    fireEvent.click(screen.getByRole("button", { name: "Sort by ID" }));
    fireEvent.click(screen.getByRole("button", { name: /^Sort by ID/ }));
    search("class:car");
    expect(ids()).toEqual(["9", "7"]);
    fireEvent.click(screen.getAllByRole("option")[0]);
    expect(edges()).toEqual([true, false]);
    fireEvent.click(button("Next in list"));
    expect(title()).toBe("7");
    expect(edges()).toEqual([false, true]);
    expect(selected).toEqual(["9", "7"]);

    play(fullSample([{ id: "9", class: "car", score: 0.6, confidence: 0.9, box: [0, 0, 1, 1] }]));
    expect(edges()).toEqual([true, true]);
    play(FRAME);
    expect(edges()).toEqual([false, true]);
    expect(source.seek).not.toHaveBeenCalled();
  });

  it("steps through the list it was opened from after the panel is closed and opened again", () => {
    const { answer } = mount();
    showFile();
    search("class:car");
    const rows = [file("7", 3), file("9", 8), file("40", 55)];
    answer(rows);
    fireEvent.click(screen.getAllByRole("option")[1]);
    fireEvent.click(button("Close media tools"));
    fireEvent.click(button("Open media tools"));
    expect(title()).toBe("9");
    expect(screen.getByRole("radio", { name: "File" })).toHaveProperty("checked", true);
    expect(screen.getByRole("textbox", { name: "Search objects" })).toHaveProperty(
      "value",
      "class:car",
    );
    answer(rows);
    expect(edges()).toEqual([false, false]);
    fireEvent.click(button("Next in list"));
    expect(title()).toBe("40");
  });

  it("asks File scope for the rows around the object it steps from, again after a close and reopen", () => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(20);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(200);
    const { queries, answer, selected, source } = mount();
    const rows = (from: number, to: number) =>
      Array.from({ length: to - from }, (_, i) => file(`r${from + i}`, from + i));
    const all = { total: 1000, matched: 1000 };
    showFile();
    answer(rows(0, 200), all);
    const list = screen.getByRole("listbox").closest(".overflow-y-auto") as HTMLElement;
    Object.defineProperty(list, "scrollTop", { value: 208 * 20, configurable: true });
    fireEvent.scroll(list);
    answer(rows(200, 400), { ...all, offset: 200 });
    fireEvent.click(screen.getByText("r200"));
    // Row 199 is not loaded yet, so there is nothing to step to.
    expect(edges()).toEqual([true, false]);
    answer(rows(150, 350), { ...all, offset: 150 });
    expect(queries().at(-1)).toMatchObject({ offset: 150 });
    expect(edges()).toEqual([false, false]);
    fireEvent.click(button("Previous in list"));
    expect(title()).toBe("r199");
    expect(selected).toEqual(["r200", "r199"]);
    // Only the row's own activation went to the object's first frame; the step did not.
    expect(source.seek.mock.calls).toEqual([[200]]);
    fireEvent.click(button("Close media tools"));
    fireEvent.click(button("Open media tools"));
    answer(rows(100, 300), { ...all, offset: 100 });
    expect(queries().at(-1)).toMatchObject({ offset: 100 });
    expect(edges()).toEqual([false, false]);
    fireEvent.click(button("Next in list"));
    expect(title()).toBe("r200");
    // A new search moves the object from row 200 to row 40, outside the rows asked for. The backend says where it is,
    // and the rows around that are asked for, so the page can still step.
    search("class:car");
    const later = (from: number, to: number) =>
      Array.from({ length: to - from }, (_, i) =>
        file(from + i === 40 ? "r200" : `n${from + i}`, from + i),
      );
    const moved = { ...all, matched: 300, located: { id: "r200", row: 40 } };
    answer(later(150, 350), { ...moved, offset: 150 });
    expect(queries().at(-1)).toMatchObject({ offset: 150, locate: "r200" });
    answer(later(0, 200), moved);
    expect(queries().at(-1)).toMatchObject({ offset: 0, locate: "r200" });
    expect(edges()).toEqual([false, false]);
    fireEvent.click(button("Next in list"));
    expect(title()).toBe("n41");
  });
});
