import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { Column } from "./columns";
import { Table } from "./Table";

type Row = { id: string; name: string; value: number };

const COLUMNS: Column<Row>[] = [
  { key: "id", title: "ID", kind: "id", text: (row) => row.id, sortable: true },
  { key: "name", title: "Name", kind: "text", text: (row) => row.name, stretch: true },
  { key: "value", title: "Value", kind: "number", text: (row) => String(row.value) },
];

const rowAt = (index: number): Row => ({ id: `row-${index}`, name: `Name ${index}`, value: index });

function table(
  tableKey: string,
  count = 100_000,
  order: readonly Row[] | null = null,
  onActivate: (row: Row) => void = () => {},
) {
  return (
    <Table<Row>
      tableKey={tableKey}
      label="Rows"
      columns={COLUMNS}
      count={count}
      rowAt={(index) => (order ? order[index] : rowAt(index))}
      keyOf={(row) => row.id}
      indexOf={(key) => (order ? order.findIndex((row) => row.id === key) : Number(key.slice(4)))}
      selected={null}
      sort={{ key: "id", descending: false }}
      onSort={() => {}}
      onActivate={onActivate}
      cursorId={(row) => `row:${row.id}`}
    />
  );
}

/** Gives every element the size of a 20-pixel row list that is 200 pixels tall and 600 wide. */
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", undefined);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(20);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(200);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(600);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Table", () => {
  it("draws only the rows in view, however many there are", () => {
    render(table("windowed"));
    const drawn = screen.getAllByRole("option");
    // Ten rows are in view; a few more on each side keep scrolling free of gaps.
    expect(drawn.length).toBeGreaterThanOrEqual(10);
    expect(drawn.length).toBeLessThanOrEqual(10 + 2 * 8);
    expect(drawn[0].getAttribute("aria-setsize")).toBe("100000");
  });

  it("keeps the cursor on its row while the rows change places", () => {
    const [a, b, c] = [rowAt(1), rowAt(2), rowAt(3)];
    const onActivate = vi.fn();
    const { rerender } = render(table("moving", 3, [a, b, c], onActivate));
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "ArrowDown" });
    rerender(table("moving", 3, [c, a, b], onActivate));
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Enter" });
    expect(onActivate).toHaveBeenCalledWith(b, 2);

    // A cursor whose row has gone is on no row, so Enter activates nothing.
    rerender(table("moving", 2, [c, a], onActivate));
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Enter" });
    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it("goes to a row that is not loaded and stays on it once it is, wherever the rows then go", () => {
    const rows = Array.from({ length: 40 }, (_, i) => rowAt(i));
    const onActivate = vi.fn();
    const { rerender } = render(table("unloaded", 40, rows.slice(0, 5), onActivate));
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "End" });
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Enter" });
    expect(onActivate).not.toHaveBeenCalled();

    rerender(table("unloaded", 40, rows, onActivate));
    rerender(table("unloaded", 40, [...rows].reverse(), onActivate));
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Enter" });
    expect(onActivate).toHaveBeenCalledWith(rows[39], 0);
  });

  it("keeps the width the user gave a column when the table is drawn again", () => {
    const width = () =>
      Number(screen.getByRole("separator", { name: "Resize ID" }).getAttribute("aria-valuenow"));
    const { unmount } = render(table("resized"));
    // The last column ends at the table's edge: nothing to resize it against.
    expect(screen.queryByRole("separator", { name: "Resize Value" })).toBeNull();
    const before = width();
    expect(before).toBeGreaterThan(0);
    fireEvent.keyDown(screen.getByRole("separator", { name: "Resize ID" }), { key: "ArrowRight" });
    const after = width();
    expect(after).toBeGreaterThan(before);

    unmount();
    render(table("resized"));
    expect(width()).toBe(after);
    cleanup();
    render(table("another"));
    expect(width()).toBe(before);
  });
});

describe("Table following a line", () => {
  const props = {
    tableKey: "followed",
    label: "Rows",
    columns: COLUMNS,
    count: 1000,
    rowAt,
    keyOf: (row: Row) => row.id,
    indexOf: (key: string) => Number(key.slice(4)),
    selected: null,
    sort: { key: "id", descending: false },
    onSort: () => {},
    onActivate: () => {},
    cursorId: (row: Row) => `row:${row.id}`,
  };
  const scroller = () => screen.getByRole("listbox").closest<HTMLElement>(".overflow-y-auto")!;

  it("keeps the line in view while following, stops when the user scrolls it away and follows again when they bring it back", () => {
    // jsdom does not scroll: a scroller that remembers where it was put.
    const tops = new WeakMap<Element, number>();
    vi.spyOn(HTMLElement.prototype, "scrollTop", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      return tops.get(this) ?? 0;
    });
    vi.spyOn(HTMLElement.prototype, "scrollTop", "set").mockImplementation(function (
      this: HTMLElement,
      value: number,
    ) {
      tops.set(this, value);
    });
    const onFollowing = vi.fn();
    const at = (line: number, following: boolean) => (
      <Table<Row> {...props} follow={{ line, keep: "third", following, onFollowing }} />
    );
    const { rerender } = render(at(100, true));
    // The row above the line goes a third of the view (200 tall, rows 20) from the top.
    expect(scroller().scrollTop).toBe(99 * 20 - 66);
    fireEvent.scroll(scroller());
    expect(onFollowing).not.toHaveBeenCalled();

    // The user scrolls to the top: the line is out of view, and following ends.
    tops.set(scroller(), 0);
    fireEvent.scroll(scroller());
    expect(onFollowing).toHaveBeenLastCalledWith(false);
    rerender(at(120, false));
    expect(scroller().scrollTop).toBe(0);

    // They scroll back to where it is: following begins again, and the line moving on is followed only once it leaves.
    tops.set(scroller(), 119 * 20 - 100);
    fireEvent.scroll(scroller());
    expect(onFollowing).toHaveBeenLastCalledWith(true);
    rerender(at(121, true));
    expect(scroller().scrollTop).toBe(119 * 20 - 100);
    rerender(at(400, true));
    expect(scroller().scrollTop).toBe(399 * 20 - 66);
  });
});
