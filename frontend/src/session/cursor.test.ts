import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import type { CursorPoint } from "../generated/api";
import { cursorPointAt, placeCursor, type Scopes } from "./cursor";

type Box = [left: number, top: number, width: number, height: number];

/**
 * A window with a title bar of `width` pixels: a fixed menu button on the left, a stretchy drag strip, a fixed close
 * button on the right. jsdom does no layout, so each element's box comes from `boxes`, and the element at a point is
 * the last one whose box holds it.
 */
function layout(width: number, boxes: Record<string, Box> = {}) {
  document.body.innerHTML = `
    <header data-cursor-region="titleBar">
      <button data-cursor-id="menu:File"></button>
      <div data-cursor-id="title:drag"></div>
      <button data-cursor-id="window:close"></button>
    </header>`;
  const all: Record<string, Box> = {
    titleBar: [0, 0, width, 20],
    "menu:File": [0, 0, 40, 20],
    "title:drag": [40, 0, width - 80, 20],
    "window:close": [width - 40, 0, 40, 20],
    ...boxes,
  };
  const key = (element: Element) =>
    (element as HTMLElement).dataset.cursorId ??
    (element as HTMLElement).dataset.cursorRegion ??
    "";
  const rect = (element: Element) => {
    const [left, top, w, h] = all[key(element)] ?? [0, 0, 0, 0];
    return new DOMRect(left, top, w, h);
  };
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    return rect(this);
  });
  document.elementFromPoint = (x, y) =>
    [...document.querySelectorAll("*")]
      .filter((element) => {
        const { left, top, right, bottom } = rect(element);
        return x >= left && x < right && y >= top && y < bottom;
      })
      .pop() ?? null;
  return (id: string) => document.querySelector(`[data-cursor-id="${id}"]`)!;
}

const TITLE: Scopes = { titleBar: "" };

afterEach(() => vi.restoreAllMocks());

describe("cursor placement", () => {
  it("lands on the same button in a window of another width", () => {
    const narrow = layout(400);
    const sent = cursorPointAt(narrow("window:close"), 380, 5, TITLE);
    expect(sent?.element).toEqual({ id: "window:close", x: 0.5, y: 0.25 });

    layout(900);
    expect(placeCursor(sent!, TITLE, document)).toEqual({ x: 880, y: 5 });
  });

  it("uses the region only without an element id and for the same scope", () => {
    layout(400);
    const sidebar: CursorPoint = { region: "titleBar", scope: "live", x: 0.5, y: 0.25 };
    expect(placeCursor(sidebar, { titleBar: "live" }, document)).toEqual({ x: 200, y: 5 });
    expect(placeCursor(sidebar, { titleBar: "workspace" }, document)).toBeNull();
    // An element id that is missing here hides the cursor instead of falling back to the region.
    const lost = { ...sidebar, element: { id: "menu:Gone", x: 0, y: 0 } };
    expect(placeCursor(lost, { titleBar: "live" }, document)).toBeNull();
  });

  it("hides a cursor whose element has no size or is covered", () => {
    const point: CursorPoint = {
      region: "titleBar",
      scope: "",
      x: 0,
      y: 0,
      element: { id: "menu:File", x: 0.5, y: 0.5 },
    };
    layout(400);
    expect(placeCursor(point, TITLE, document)).toEqual({ x: 20, y: 10 });

    layout(400, { "menu:File": [0, 0, 0, 20] });
    expect(placeCursor(point, TITLE, document)).toBeNull();

    // A dialog on top of the title bar, or the button scrolled out of its container.
    layout(400, { "title:drag": [0, 0, 400, 20] });
    expect(placeCursor(point, TITLE, document)).toBeNull();
  });
});
