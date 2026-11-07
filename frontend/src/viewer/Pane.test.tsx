import { useMemo, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { NO_ACTIONS, ViewerActionsContext, useViewerActions, type ViewerActions } from "./actions";
import { Pane } from "./Pane";

afterEach(cleanup);

/** A viewer that offers one command named after its pane. */
function Viewer({ pane }: { pane: number }) {
  useViewerActions(useMemo(() => ({ [`play-${pane}`]: () => undefined }), [pane]));
  return null;
}

/** Two panes under a shell that keeps the commands offered to it, and lists them. */
function Panes({ active, onActivate }: { active: number; onActivate: (pane: number) => void }) {
  const [offered, setOffered] = useState<ViewerActions>(NO_ACTIONS);
  return (
    <ViewerActionsContext.Provider value={setOffered}>
      <output aria-label="Offered">{Object.keys(offered).join()}</output>
      {[1, 2].map((pane) => (
        <Pane
          key={pane}
          viewer={pane}
          title={`pane ${pane}`}
          pinned={false}
          active={pane === active}
          rect={{ x: 0, y: 0, w: 100, h: 100 }}
          canPin
          canClose
          dragged={{ current: null }}
          onActivate={() => onActivate(pane)}
          onPin={() => undefined}
          onClose={() => undefined}
          onDrop={() => undefined}
        >
          <Viewer pane={pane} />
        </Pane>
      ))}
    </ViewerActionsContext.Provider>
  );
}

describe("pane", () => {
  it("offers its viewer's commands to the shell only while it is the active pane", () => {
    const offered = () => screen.getByLabelText("Offered").textContent;
    const { rerender } = render(<Panes active={1} onActivate={() => undefined} />);
    expect(offered()).toBe("play-1");
    rerender(<Panes active={2} onActivate={() => undefined} />);
    expect(offered()).toBe("play-2");
  });

  it("becomes active when it is pressed or focused, but not when it already is", () => {
    const onActivate = vi.fn();
    render(<Panes active={1} onActivate={onActivate} />);
    fireEvent.pointerDown(screen.getByRole("group", { name: "pane 1" }));
    expect(onActivate).not.toHaveBeenCalled();
    fireEvent.pointerDown(screen.getByRole("group", { name: "pane 2" }));
    expect(onActivate).toHaveBeenLastCalledWith(2);
    fireEvent.focus(screen.getAllByRole("button", { name: "Close this pane" })[1]);
    expect(onActivate).toHaveBeenCalledTimes(2);
  });
});
