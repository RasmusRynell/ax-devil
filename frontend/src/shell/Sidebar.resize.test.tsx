import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider, useView } from "../session/session";
import { KeptStateProvider } from "./keptState";
import { Sidebar } from "./Sidebar";

/** Whether the session's view has the sidebar open. */
function Open() {
  const [{ open }] = useView("sidebar");
  return <output aria-label="open">{String(open)}</output>;
}

function page(shown: boolean) {
  return (
    <SessionProvider>
      <KeptStateProvider>
        <Open />
        <div>
          <nav aria-label="Activity" />
          {shown && <Sidebar view="plugins" onRun={() => {}} onAddLive={() => {}} />}
        </div>
      </KeptStateProvider>
    </SessionProvider>
  );
}

// A 1000-wide row with a 40-wide activity strip, so the sidebar may take 480, and its content needs 200.
beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ width: 300 }) as DOMRect,
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (
    this: HTMLElement,
  ) {
    return { ASIDE: 300, NAV: 40 }[this.tagName] ?? 1000;
  });
  Element.prototype.setPointerCapture = vi.fn();
  // jsdom has no pointer events; a mouse event carries the position.
  vi.stubGlobal("PointerEvent", MouseEvent);
  document.head.insertAdjacentHTML("beforeend", "<style>aside { min-width: 200px }</style>");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.head.querySelector("style")?.remove();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

describe("Resizing the sidebar", () => {
  const divider = () => screen.getByRole("separator", { name: "Resize sidebar" });
  const width = () => (screen.getByRole("complementary") as HTMLElement).style.cssText;

  it("stays between what its view needs and half the space beside the activity bar", async () => {
    render(page(true));
    await act(async () => {});
    expect(divider().getAttribute("aria-valuemin")).toBe("200");
    expect(divider().getAttribute("aria-valuemax")).toBe("480");
    fireEvent.keyDown(divider(), { key: "End" });
    expect(width()).toContain("480px");
    fireEvent.keyDown(divider(), { key: "Home" });
    expect(width()).toContain("200px");
  });

  it("keeps a dragged width through hiding and showing the sidebar", async () => {
    const { rerender } = render(page(true));
    await act(async () => {});
    fireEvent.pointerDown(divider(), { pointerId: 1, clientX: 300 });
    fireEvent.pointerMove(divider(), { pointerId: 1, clientX: 400, buttons: 1 });
    fireEvent.pointerUp(divider(), { pointerId: 1, clientX: 400 });
    expect(width()).toContain("400px");

    rerender(page(false));
    expect(screen.queryByRole("complementary")).toBeNull();
    rerender(page(true));
    expect(width()).toContain("400px");
  });

  it("hides the sidebar when a drag ends well below the least width, and keeps the last width", async () => {
    const { rerender } = render(page(true));
    await act(async () => {});
    fireEvent.pointerDown(divider(), { pointerId: 1, clientX: 300 });
    fireEvent.pointerMove(divider(), { pointerId: 1, clientX: 250, buttons: 1 });
    fireEvent.pointerMove(divider(), { pointerId: 1, clientX: 50, buttons: 1 });
    expect(screen.getByLabelText("open").textContent).toBe("true");
    fireEvent.pointerUp(divider(), { pointerId: 1, clientX: 50 });
    expect(screen.getByLabelText("open").textContent).toBe("false");

    rerender(page(true));
    expect(width()).toContain("200px");
  });
});
