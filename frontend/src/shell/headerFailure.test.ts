import { describe, expect, it } from "vite-plus/test";
import { act, renderHook } from "@testing-library/react";
import type { View } from "../generated/api";
import { scenario } from "../session/fakeScenario";
import { NOTHING_HIDDEN } from "../viewer/hidden";
import { useHeaderFailure } from "./headerFailure";
import type { Viewer } from "../viewer/useViewers";

const reveal = scenario("reveal");
const { mot, other } = reveal.ids;
const [A1, A2, B1] = [reveal.ids["mot/A"], reveal.ids["mot/B"], reveal.ids["other/X"]];
const items = reveal.doc().workspace.items;

/** A viewer of the playlist item `item`: `mot` of the entries A and B, or `other` of the entry X. */
const viewer = (id: number, item: string): Viewer => ({
  id,
  item: items.find((found) => found.id === item)!,
  pinned: false,
});
const views = (shown: Record<number, string>): View["viewers"] =>
  Object.fromEntries(
    Object.entries(shown).map(([id, entry]) => [id, { item: "x", pinned: false, entry }]),
  ) as View["viewers"];

const A = { viewer: 1, item: mot, entry: A1 };
const B = { viewer: 2, item: other, entry: B1 };
const BOTH = { viewers: [viewer(1, mot), viewer(2, other)], views: views({ 1: A1, 2: B1 }) };
// A view that names no entry yet shows the first one: the same pane as one that names it.
const ENTRYLESS = { ...BOTH, views: {} as View["viewers"] };

function setup(initial = BOTH) {
  return renderHook(
    (props: typeof BOTH) => useHeaderFailure(props.viewers, props.views, NOTHING_HIDDEN),
    {
      initialProps: initial,
    },
  );
}

describe("a header action's failure", () => {
  it("counts only for the newest action: a late result of an older one is dropped", () => {
    const { result } = setup();
    let sayA!: ReturnType<typeof result.current.begin>;
    let sayB!: ReturnType<typeof result.current.begin>;
    act(() => {
      sayA = result.current.begin(A);
      sayB = result.current.begin(B);
    });
    // A's rejection arrives after B's action started: no banner in B, none in A.
    act(() => sayA("A failed"));
    expect(result.current.failure).toBeNull();
    act(() => sayB("B failed"));
    expect(result.current.failure).toMatchObject({ viewer: 2, message: "B failed" });

    // An older success does not clear a newer failure.
    let older!: ReturnType<typeof result.current.begin>;
    let newer!: ReturnType<typeof result.current.begin>;
    act(() => {
      older = result.current.begin(A);
      newer = result.current.begin(A);
    });
    act(() => newer("A failed again"));
    act(() => older(null));
    expect(result.current.failure).toMatchObject({ viewer: 1, message: "A failed again" });
    // The next action to start clears it, and a success of its own keeps it clear.
    act(() => result.current.begin(B)(null));
    expect(result.current.failure).toBeNull();
  });

  it("is reset, not hidden, when its pane shows another entry or goes, and does not return with them", () => {
    const { result, rerender } = setup();
    const fail = () => act(() => result.current.begin(A)("failed"));
    fail();
    expect(result.current.failure).not.toBeNull();
    // Another entry, then back to the first.
    rerender({ ...BOTH, views: views({ 1: A2, 2: B1 }) });
    expect(result.current.failure).toBeNull();
    rerender(BOTH);
    expect(result.current.failure).toBeNull();

    // The pane closes and a new one takes its id for the same item and entry.
    fail();
    rerender({ viewers: [viewer(2, other)], views: views({ 2: B1 }) });
    expect(result.current.failure).toBeNull();
    rerender(BOTH);
    expect(result.current.failure).toBeNull();
  });

  it("keeps a failure while the view names the entry the pane was already showing", () => {
    const { result, rerender } = setup(ENTRYLESS);
    act(() => result.current.begin(A)("failed"));
    expect(result.current.failure).not.toBeNull();
    rerender(BOTH);
    expect(result.current.failure).not.toBeNull();
  });

  it("does not take a late result once its action's pane was replaced or moved on and came back", () => {
    const { result, rerender, unmount } = setup();
    const start = () => {
      let say!: ReturnType<typeof result.current.begin>;
      act(() => {
        say = result.current.begin(A);
      });
      return say;
    };
    // The pane closes while another stays, then the same item opens into the reused id; the response comes after.
    let say = start();
    rerender({ viewers: [viewer(2, other)], views: views({ 2: B1 }) });
    rerender(BOTH);
    act(() => say("late"));
    expect(result.current.failure).toBeNull();

    // Another entry and back before the response.
    say = start();
    rerender({ ...BOTH, views: views({ 1: A2, 2: B1 }) });
    rerender(BOTH);
    act(() => say("late"));
    expect(result.current.failure).toBeNull();

    // A response after the area is gone does nothing.
    say = start();
    unmount();
    expect(() => say("late")).not.toThrow();
  });

  it("still shows the failure of an action in flight when Escape, Dismiss or a menu took down a banner", () => {
    const { result } = setup();
    for (const takeDown of [
      () => void document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })),
      () => result.current.clear(),
    ]) {
      let say!: ReturnType<typeof result.current.begin>;
      act(() => {
        say = result.current.begin(A);
      });
      act(takeDown);
      act(() => say("refused"));
      expect(result.current.failure).toMatchObject({ viewer: 1, message: "refused" });
    }
    // A pane that went meanwhile is another matter: the user left, and the failure is dropped.
  });

  it("drops a result for a pane that is gone, and clears on Dismiss and on Escape", () => {
    const { result, rerender } = setup();
    let say!: ReturnType<typeof result.current.begin>;
    act(() => {
      say = result.current.begin(A);
    });
    rerender({ viewers: [viewer(2, other)], views: views({ 2: B1 }) });
    act(() => say("too late"));
    expect(result.current.failure).toBeNull();
    rerender(BOTH);

    act(() => result.current.begin(A)("failed"));
    act(() => result.current.clear());
    expect(result.current.failure).toBeNull();
    act(() => result.current.begin(A)("failed"));
    act(() => void document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
    expect(result.current.failure).toBeNull();
  });
});
