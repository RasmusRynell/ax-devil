import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { FileScopeUnavailable } from "../generated/api";
import { fullSample } from "../present/fixtures";
import { file, ids, mount, says, search, showFile } from "./panelRig";

beforeEach(() => vi.stubGlobal("ResizeObserver", undefined));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Frame scope", () => {
  it("lists the frame's objects, best score first, searchable and sortable, and a row selects its object", () => {
    const { source, selected } = mount();
    expect(ids()).toEqual(["12", "7", "9"]);
    // Natural order: 9 comes before 12, as it does not as text.
    fireEvent.click(screen.getByRole("button", { name: "Sort by ID" }));
    expect(ids()).toEqual(["7", "9", "12"]);
    fireEvent.click(screen.getByRole("button", { name: /^Sort by ID/ }));
    expect(ids()).toEqual(["12", "9", "7"]);

    // Another column starts ascending, and each row reads its values off the frame.
    fireEvent.click(screen.getByRole("button", { name: "Sort by Conf" }));
    expect(ids()).toEqual(["7", "12", "9"]);
    expect(screen.getAllByRole("option")[1].textContent).toContain("0.50");
    expect(screen.getAllByRole("option")[1].textContent).toContain("moving");
    fireEvent.click(screen.getByRole("button", { name: "Sort by ID" }));

    search("class:car score>0.7");
    expect(ids()).toEqual(["7"]);
    expect(screen.getByText("1 of 3")).toBeTruthy();
    search("class:bus");
    expect(screen.getByText("No matching objects")).toBeTruthy();

    // Motion is searched as the inspection names it, `motion.state`.
    search("state:moving");
    expect(ids()).toEqual(["12"]);

    search("");
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Enter" });
    expect(selected).toEqual(["9"]);
    expect(source.seek).not.toHaveBeenCalled();
  });

  it("filters 150 ms after typing stops, at once on Return or Clear, and Clear shows only with text", () => {
    vi.useFakeTimers();
    mount();
    const box = screen.getByRole("textbox", { name: "Search objects" });
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
    fireEvent.change(box, { target: { value: "class:car score>0.7" } });
    act(() => {
      vi.advanceTimersByTime(149);
    });
    expect(ids()).toEqual(["12", "7", "9"]);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(ids()).toEqual(["7"]);

    fireEvent.change(box, { target: { value: "id:12" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(ids()).toEqual(["12"]);

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(ids()).toEqual(["12", "7", "9"]);
    expect(document.activeElement).toBe(box);
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  });

  it("ignores Return that confirms an input-method candidate, and opens the syntax help with focus in it", () => {
    mount();
    const box = screen.getByRole("textbox", { name: "Search objects" });
    fireEvent.change(box, { target: { value: "id:12" } });
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    expect(ids()).toEqual(["12", "7", "9"]);
    fireEvent.keyDown(box, { key: "Enter" });
    expect(ids()).toEqual(["12"]);

    fireEvent.click(screen.getByRole("button", { name: "Query syntax" }));
    expect(document.activeElement).toBe(
      screen.getByRole("dialog", { name: "Query syntax" }).firstElementChild,
    );
  });

  it("names a searched field no object has, only where it can tell", () => {
    mount(
      {},
      { frame: fullSample([{ id: "7", class: "car", score: 0.8, box: [0.1, 0.1, 0.3, 0.3] }]) },
    );
    // Motion state and confidence are in what is searched here, and the object has neither.
    search("state:moving confidence>0.5");
    expect(says("No object has state, confidence in this frame")).toBe(true);
    // Any other field may be in the inspection data, so its absence is not claimed.
    search("speed>3");
    expect(
      screen
        .queryAllByRole("status")
        .some((status) => status.textContent?.startsWith("No object has")),
    ).toBe(false);
  });

  it("is where it was left, cursor and scroll, when the scope is shown again", () => {
    // Each scroll container keeps the position it is given, as in a browser.
    const tops = new WeakMap<Element, number>();
    vi.spyOn(Element.prototype, "scrollTop", "get").mockImplementation(function (this: Element) {
      return tops.get(this) ?? 0;
    });
    vi.spyOn(Element.prototype, "scrollTop", "set").mockImplementation(function (
      this: Element,
      value: number,
    ) {
      tops.set(this, value);
    });
    const { selected, answer } = mount();
    const list = () => screen.getByRole("listbox");
    const scroller = () => list().parentElement!.parentElement!;
    fireEvent.keyDown(list(), { key: "ArrowDown" });
    scroller().scrollTop = 120;
    fireEvent.scroll(scroller());

    showFile();
    answer([file("50", 3), file("51", 4), file("52", 5)]);
    fireEvent.keyDown(list(), { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("radio", { name: "Frame" }));
    expect(scroller().scrollTop).toBe(120);
    fireEvent.keyDown(list(), { key: "Enter" });
    expect(selected).toEqual(["7"]);
    fireEvent.keyDown(screen.getByRole("region", { name: "Object page" }), { key: "Escape" });

    // File scope's cursor row is not in the page listed next: Enter opens no other object.
    showFile();
    answer([file("60", 3), file("61", 4), file("62", 5)]);
    fireEvent.keyDown(list(), { key: "Enter" });
    expect(selected).toEqual(["7", null]);
  });
});

describe("File scope", () => {
  it("asks for the typed search and sort, lists the rows, and dims what is not in the frame", () => {
    const { source, selected, queries, answer } = mount();
    showFile();
    search("class:car");
    answer([file("7", 3), file("40", 55)], { total: 90, matched: 2 });
    expect(queries().map((q) => (q.type === "FileQuery" ? [q.query, q.sort] : []))).toEqual([
      [
        {
          groups: [[{ path: "class", op: "eq", value: "car", negated: false }]],
        },
        { key: "id", descending: false },
      ],
    ]);
    expect(ids()).toEqual(["7", "40"]);
    expect(screen.getByText("2 of 90")).toBeTruthy();
    const [here, away] = screen.getAllByRole("option");
    expect(here.textContent).toContain("3–23");

    // An object in the frame is only selected; one that is not also goes to where it first is.
    fireEvent.click(here);
    expect(selected).toEqual(["7"]);
    expect(source.seek).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole("region", { name: "Object page" }), { key: "Escape" });
    fireEvent.click(away);
    expect(selected).toEqual(["7", null, "40"]);
    expect(source.seek).toHaveBeenCalledWith(55);

    // Return and Clear ask again without the pause typing has.
    const asked = queries().length;
    search("id:7");
    expect(queries()).toHaveLength(asked + 1);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(queries()).toHaveLength(asked + 2);

    // A field outside what File scope searches is said so.
    search("score>0.5");
    expect(says("File scope filters id, class and event only")).toBe(true);
    search("event:motion");
    expect(says("File scope filters id, class and event only")).toBe(false);
  });

  it("keeps the row the cursor is on when the list is scrolled away, and follows it through a search", () => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(20);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(200);
    const { selected, queries, answer } = mount();
    const rows = (from: number, to: number) =>
      Array.from({ length: to - from }, (_, i) => file(`r${from + i}`, from + i));
    const big = { total: 1000, matched: 1000 };
    const last = () => queries().at(-1) as { query: unknown; offset: number; locate: string };
    showFile();
    answer(rows(0, 20), big);
    // The cursor goes onto r5 with a click; the page it opens is left again.
    fireEvent.click(screen.getAllByRole("option")[5]);
    fireEvent.keyDown(screen.getByRole("region", { name: "Object page" }), { key: "Escape" });

    const list = screen.getByRole("listbox");
    const scroller = list.parentElement!.parentElement!;
    Object.defineProperty(scroller, "scrollTop", { value: 8000, writable: true });
    fireEvent.scroll(scroller);
    answer(rows(350, 370), { ...big, offset: 350, located: { id: "r5", row: 5 } });
    expect(last()).toMatchObject({ offset: 350, locate: "r5" });

    // Row 6 is not loaded: the list goes there, and Enter opens it once it is.
    fireEvent.keyDown(list, { key: "ArrowDown" });
    answer(rows(0, 20), big);
    expect(last().offset).toBe(0);
    fireEvent.keyDown(list, { key: "Enter" });
    expect(selected.at(-1)).toBe("r6");
    fireEvent.keyDown(screen.getByRole("region", { name: "Object page" }), { key: "Escape" });

    // A search that moves the cursor's row far down is followed there.
    search("class:car");
    answer(rows(30, 50), { ...big, offset: 30, located: { id: "r6", row: 40 } });
    expect(last().locate).toBe("r6");
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(selected.at(-1)).toBe("r41");
    fireEvent.keyDown(screen.getByRole("region", { name: "Object page" }), { key: "Escape" });

    // A cursor that moves while a new sort is awaited is what that question locates.
    fireEvent.click(screen.getByRole("button", { name: /^Sort by ID/ }));
    fireEvent.keyDown(list, { key: "ArrowDown" });
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(last().locate).toBe("r42");
  });

  it("seeks to the frame under a click on the presence strip", () => {
    const { source, selected, answer } = mount();
    showFile();
    answer([file("7", 3)]);
    const strip = screen.getByRole("option").children[2].firstElementChild as HTMLElement;
    vi.spyOn(strip, "getBoundingClientRect").mockReturnValue({ left: 10, width: 200 } as DOMRect);
    fireEvent.click(strip, { clientX: 110 });
    expect(source.seek).toHaveBeenCalledWith(50);
    expect(selected).toEqual([]);
  });

  it.each<[FileScopeUnavailable, string]>([
    [{ state: "Reading" }, "Reading the file…"],
    [{ state: "Following" }, "Not available while following"],
    [{ state: "TooLarge" }, "The file is too large to list"],
    [{ state: "PlainLane" }, "This lane has no objects"],
    [{ state: "Failed", code: "Internal", params: {}, reason: "Disk full" }, "Disk full"],
  ])("says why there are no rows: %j", (unavailable, text) => {
    const { files, queries } = mount();
    showFile();
    expect(screen.getByText("Loading…")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(150);
    });
    const request = (queries().at(-1) as { request: number }).request;
    act(() => files.receive({ type: "FileScope", lane: 0, request, unavailable }));
    expect(screen.getByText(text)).toBeTruthy();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("stops asking for the list when Frame scope is shown again or the panel goes away", () => {
    const { files, answer, unmount } = mount();
    showFile();
    answer([file("7", 3)]);
    expect(files.view(0).page).not.toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Frame" }));
    expect(files.view(0)).toEqual({ pending: false, page: null, unavailable: null });
    showFile();
    expect(files.view(0).pending).toBe(true);
    unmount();
    expect(files.view(0)).toEqual({ pending: false, page: null, unavailable: null });
  });
});
describe("Copying", () => {
  const copyRow = async (writeText: () => Promise<void>) => {
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    mount();
    fireEvent.keyDown(screen.getByRole("listbox", { name: "Objects" }), {
      key: "c",
      ctrlKey: true,
    });
    await act(async () => {});
  };

  it("copies a row's whole text on Ctrl+C and confirms once the clipboard has it", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    await copyRow(writeText);
    // The best-scored object, every column, hidden ones too.
    expect(writeText).toHaveBeenCalledWith("12\thuman\t0.90\t0.50\tmoving");
    expect(screen.getByText("Copied")).toBeTruthy();
  });

  it("confirms nothing where the clipboard refuses", async () => {
    await copyRow(() => Promise.reject(new Error("insecure origin")));
    expect(screen.queryByText("Copied")).toBeNull();
  });
});

describe("Resizing", () => {
  let panelWidth = 400;
  beforeEach(() => {
    panelWidth = 400;
    // The panel is 400 wide with a 300-wide video beside it, and its content needs 280.
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
      () => ({ width: panelWidth }) as DOMRect,
    );
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(300);
    document.head.insertAdjacentHTML("beforeend", "<style>aside { min-width: 280px }</style>");
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.head.querySelector("style")?.remove();
  });

  const divider = () => screen.getByRole("separator", { name: "Resize media tools" });

  it("keeps the content whole and the video 160 wide, and the chosen width through a close", () => {
    const onWidth = vi.fn();
    const { unmount } = mount({ onWidth });
    expect(divider().getAttribute("aria-valuemin")).toBe("280");
    expect(divider().getAttribute("aria-valuemax")).toBe("540");

    fireEvent.keyDown(divider(), { key: "ArrowLeft" });
    const wider = onWidth.mock.calls.at(-1)?.[0] as number;
    expect(wider).toBeGreaterThan(400);
    fireEvent.keyDown(divider(), { key: "End" });
    expect(onWidth).toHaveBeenLastCalledWith(540);
    fireEvent.keyDown(divider(), { key: "Home" });
    expect(onWidth).toHaveBeenLastCalledWith(280);
    panelWidth = 280;
    fireEvent.keyDown(divider(), { key: "ArrowRight" });
    expect(onWidth).toHaveBeenLastCalledWith(280);

    // Closed and opened again, the panel takes the width it was given.
    unmount();
    mount({ width: wider });
    expect(screen.getByRole("complementary", { name: "Media tools" }).style.cssText).toContain(
      `${wider}px`,
    );
  });
});
