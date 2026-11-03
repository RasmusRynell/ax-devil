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

    // A cursor whose row has gone stays where it was, within the rows left.
    rerender(table("moving", 2, [c, a], onActivate));
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Enter" });
    expect(onActivate).toHaveBeenLastCalledWith(a, 1);
  });

  it("keeps the width the user gave a column when the table is drawn again", () => {
    const width = () =>
      Number(screen.getByRole("separator", { name: "Resize ID" }).getAttribute("aria-valuenow"));
    const { unmount } = render(table("resized"));
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
