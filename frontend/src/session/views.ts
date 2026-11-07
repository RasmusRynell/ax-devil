import type { Scope } from "../commands";
import type { Can, Layout, View } from "../generated/api";

/** What the window shows before anyone has chosen anything. */
export const EMPTY_VIEW: View = { sidebar: { view: "workspace", open: true }, viewers: {} };

/** A view change the window has applied and numbered, which the backend has not settled yet. */
export interface Pending {
  seq: number;
  view: View;
}

/**
 * What the window shows. The latest change this window made that the backend has not settled (`seq > viewsDone`)
 * wins, so a click never waits for the backend, and a refusal advances `viewsDone` so the change stops winning.
 * Otherwise the backend's own: the presenter's view while following, the seat's own while exploring.
 */
export function shownView(authoritative: View | undefined, pending: Pending | null): View {
  return pending?.view ?? authoritative ?? EMPTY_VIEW;
}

const same = (a: unknown, b: unknown) => a === b || JSON.stringify(a) === JSON.stringify(b);

/**
 * `next` with every part that equals the same part of `previous` replaced by it, so parts keep their identity
 * while they do not change and a component reading one of them does not render for another's change.
 */
export function shareView(previous: View, next: View): View {
  const viewers = Object.fromEntries(
    Object.entries(next.viewers).map(([id, viewer]) => {
      const before = previous.viewers[Number(id)];
      return [id, same(before, viewer) ? before : viewer];
    }),
  ) as View["viewers"];
  const sameViewers =
    Object.keys(viewers).length === Object.keys(previous.viewers).length &&
    Object.entries(viewers).every(([id, viewer]) => previous.viewers[Number(id)] === viewer);
  return {
    sidebar: same(previous.sidebar, next.sidebar) ? previous.sidebar : next.sidebar,
    active: next.active,
    viewers: sameViewers ? previous.viewers : viewers,
    layout: same(previous.layout, next.layout) ? previous.layout : next.layout,
  };
}

/** The most panes a window lays out; the backend refuses a view with more. */
export const MAX_PANES = 8;

/**
 * `view` with a new viewer of `item` in front, beside the others in the layout: each takes an equal part of the
 * width. Absent when the window holds as many panes as it may.
 */
export function withViewer(view: View, item: string, entry?: string): View | undefined {
  const panes = Object.keys(view.viewers).length;
  if (panes >= MAX_PANES) return undefined;
  // One above the highest id, as the backend expects of the window.
  const viewer = Math.max(0, ...Object.keys(view.viewers).map(Number)) + 1;
  const pane: Layout = { type: "pane", viewer };
  const layout: Layout = view.layout
    ? {
        type: "split",
        axis: "row",
        share: Math.round((10_000 * panes) / (panes + 1)),
        first: view.layout,
        second: pane,
      }
    : pane;
  return {
    ...view,
    active: viewer,
    layout,
    viewers: { ...view.viewers, [viewer]: { item, pinned: false, ...(entry && { entry }) } },
  };
}

/** What a seat may change in `scope`; `false` before the backend has said what the seat can do. */
export function canDo(can: Can | null, scope: Scope): boolean {
  return scope === "app" || (can?.[scope] ?? false);
}
