import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, render, renderHook } from "@testing-library/react";
import { dropLaneFocus, focusLane, useLaneFocus } from "./laneFocus";
import { Pane } from "./Pane";

afterEach(() => {
  cleanup();
  focusLane(0, undefined, undefined);
});

const LABELS = ["DPM", "GT"];

/** A viewer's pane as the playback hook sees it; `rerender` changes what it shows. */
function pane(entry = "e", shown = [0, 1], active = true) {
  const apply = vi.fn();
  const hook = renderHook(
    (props) => useLaneFocus(1, props.entry, LABELS, props.shown, props.active, apply),
    { initialProps: { entry, shown, active } },
  );
  return { apply, ...hook };
}

describe("lane focus", () => {
  it("gives the keyboard to the lane named, once, when its viewer shows the entry", () => {
    const { apply } = pane();
    act(() => focusLane(1, "e", "GT"));
    expect(apply).toHaveBeenCalledExactlyOnceWith(1);
  });

  it("waits for a lane that is not shown yet", () => {
    const { apply, rerender } = pane("e", [0]);
    act(() => focusLane(1, "e", "GT"));
    expect(apply).not.toHaveBeenCalled();
    rerender({ entry: "e", shown: [0, 1], active: true });
    expect(apply).toHaveBeenCalledExactlyOnceWith(1);
  });

  it("drops a request for another entry, and leaves another viewer's alone", () => {
    const { apply, rerender } = pane("e");
    act(() => focusLane(1, "other", "GT"));
    rerender({ entry: "other", shown: [0, 1], active: true });
    expect(apply).not.toHaveBeenCalled();
    act(() => focusLane(2, "e", "GT"));
    rerender({ entry: "e", shown: [0, 1], active: true });
    expect(apply).not.toHaveBeenCalled();
    act(() => dropLaneFocus(2));
  });

  it("is dropped when the pane stops being the active one before it was carried out", () => {
    const { apply, rerender } = pane("e", [0], false);
    act(() => focusLane(1, "e", "GT"));
    render(
      <Pane
        viewer={1}
        title="v"
        pinned={false}
        active={false}
        rect={{ x: 0, y: 0, w: 10, h: 10 }}
        canPin
        canClose
        dragged={{ current: null }}
        onActivate={() => {}}
        onPin={() => {}}
        onClose={() => {}}
        onDrop={() => {}}
      >
        {null}
      </Pane>,
    );
    rerender({ entry: "e", shown: [0, 1], active: true });
    expect(apply).not.toHaveBeenCalled();
  });
});
