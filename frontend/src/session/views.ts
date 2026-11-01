import type { Scope } from "../commands";
import type { Can, View } from "../generated/api";

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
    Object.entries(next.viewers).map(([item, viewer]) => {
      const before = previous.viewers[item];
      return [item, same(before, viewer) ? before : viewer];
    }),
  ) as View["viewers"];
  const sameViewers =
    Object.keys(viewers).length === Object.keys(previous.viewers).length &&
    Object.entries(viewers).every(([item, viewer]) => previous.viewers[item] === viewer);
  return {
    sidebar: same(previous.sidebar, next.sidebar) ? previous.sidebar : next.sidebar,
    active: next.active,
    viewers: sameViewers ? previous.viewers : viewers,
  };
}

/** What a seat may change in `scope`; `false` before the backend has said what the seat can do. */
export function canDo(can: Can | null, scope: Scope): boolean {
  return scope === "app" || (can?.[scope] ?? false);
}
