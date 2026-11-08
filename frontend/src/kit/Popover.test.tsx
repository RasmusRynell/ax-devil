import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Popover } from "./Popover";

afterEach(cleanup);

describe("popover", () => {
  it("closes on a click outside without passing that click on", async () => {
    const beneath = vi.fn();
    render(
      <>
        <Popover label="Appearance" trigger="Appearance">
          <button type="button">Inside</button>
        </Popover>
        <button type="button" onClick={beneath}>
          Beneath
        </button>
      </>,
    );
    const click = (name: string) => {
      const target = screen.getByRole("button", { name });
      fireEvent.pointerDown(target);
      fireEvent.pointerUp(target);
      fireEvent.click(target);
    };
    click("Appearance");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Inside" }));
    click("Inside");
    expect(screen.queryByRole("dialog")).not.toBeNull();

    click("Beneath");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(beneath).not.toHaveBeenCalled();

    click("Beneath");
    expect(beneath).toHaveBeenCalledTimes(1);

    // A press the browser cancels never becomes a click, and must not swallow a later one.
    click("Appearance");
    fireEvent.pointerDown(screen.getByRole("button", { name: "Beneath" }));
    fireEvent.pointerCancel(screen.getByRole("button", { name: "Beneath" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    fireEvent.click(screen.getByRole("button", { name: "Beneath" }));
    expect(beneath).toHaveBeenCalledTimes(2);
  });
});

describe("popover placement", () => {
  // jsdom lays nothing out: the area, the button and the panel's content are stubbed, and the panel's own height is
  // whatever `content` says, as a browser would measure it.
  const area = { left: 50, top: 100, right: 450, bottom: 500 };
  const near = { left: 400, top: 440, right: 430, bottom: 470 };
  const wide = 200;
  let content = 200;
  // A resize reaches the callback only for an element registered through `observe()` and not since dropped.
  const observers = new Set<{ update: () => void; targets: Set<Element> }>();
  const resize = (target: Element) =>
    act(() => observers.forEach((o) => o.targets.has(target) && o.update()));

  beforeEach(() => {
    content = 200;
    observers.clear();
    vi.stubGlobal(
      "ResizeObserver",
      class {
        private readonly entry: { update: () => void; targets: Set<Element> };
        constructor(update: () => void) {
          this.entry = { update, targets: new Set() };
          observers.add(this.entry);
        }
        observe(target: Element) {
          this.entry.targets.add(target);
        }
        unobserve(target: Element) {
          this.entry.targets.delete(target);
        }
        disconnect() {
          observers.delete(this.entry);
        }
      },
    );
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
      this: Element,
    ) {
      const rect = "popoverBounds" in (this as HTMLElement).dataset ? area : near;
      return { ...rect, width: rect.right - rect.left, height: rect.bottom - rect.top } as DOMRect;
    });
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(wide);
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(() => content);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(() => content);
    vi.spyOn(Element.prototype, "scrollHeight", "get").mockImplementation(() => content);
  });
  afterEach(() => vi.restoreAllMocks());

  const open = () => {
    render(
      <div data-popover-bounds>
        <Popover label="Appearance" trigger="Appearance">
          <p>Panel</p>
        </Popover>
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Appearance" }));
    return screen.getByRole("dialog");
  };
  /** The panel's box as the browser would draw it: at most `maxHeight` tall. */
  const box = (panel: HTMLElement) => {
    const top = parseFloat(panel.style.top);
    const height = Math.min(content, parseFloat(panel.style.maxHeight));
    return { top, bottom: top + height, left: parseFloat(panel.style.left) };
  };

  it("opens above a button near the bottom of its area, inside the area", () => {
    const { top, bottom, left } = box(open());
    expect(bottom).toBeLessThanOrEqual(near.top);
    expect(top).toBeGreaterThanOrEqual(area.top);
    expect(left).toBeGreaterThanOrEqual(area.left);
    expect(left + wide).toBeLessThanOrEqual(area.right);
  });

  it("caps a panel taller than its area to the area", () => {
    content = 900;
    const panel = open();
    const { top, bottom } = box(panel);
    expect(bottom - top).toBeLessThan(content);
    expect(top).toBeGreaterThanOrEqual(area.top);
    expect(bottom).toBeLessThanOrEqual(area.bottom);
  });

  it("is placed again when its content grows", () => {
    const panel = open();
    const before = box(panel).top;
    content = 300;
    resize(panel);
    const { top, bottom } = box(panel);
    expect(top).toBeLessThan(before);
    expect(top).toBeGreaterThanOrEqual(area.top);
    expect(bottom).toBeLessThanOrEqual(near.top);
  });
});
