import type { Layout, View, ViewerView } from "../generated/api";
import { MAX_PANES } from "../session/views";
import { largest, leaves, move, prune, split, swap, type Side } from "./layout";

/** The viewer in front, if it is one the layout places. */
const frontPane = (view: View): number | undefined =>
  view.active !== undefined && leaves(view.layout).includes(view.active) ? view.active : undefined;

/** Where a new viewer goes: in place of a pane, or to the right of one, or (without a layout) alone. */
type Spot = { replacing: number } | { beside: number } | undefined;

/**
 * `view` with a new viewer at `spot`, in front. A viewer is numbered one above the highest the window has had,
 * so a replaced one is never reused. The layout places the viewers; the view keeps no other. Absent when the viewer
 * would be a ninth pane.
 */
function put(view: View, spot: Spot, shown: ViewerView): View | undefined {
  const id = Math.max(0, ...Object.keys(view.viewers).map(Number)) + 1;
  let layout: Layout;
  if (!view.layout || !spot) layout = { type: "pane", viewer: id };
  else if ("replacing" in spot) layout = swap(view.layout, spot.replacing, id);
  else if (leaves(view.layout).length >= MAX_PANES) return undefined;
  else layout = split(view.layout, spot.beside, id, "right");
  const placed = leaves(layout);
  const viewers: View["viewers"] = {};
  for (const viewer of placed) viewers[viewer] = viewer === id ? shown : view.viewers[viewer];
  return { ...view, active: id, layout, viewers };
}

const viewerOf = (item: string, entry: string | undefined, pinned: boolean): ViewerView => ({
  item,
  pinned,
  ...(entry && { entry }),
});

/**
 * Opens an item in a window that has not been told where: beside the largest pane, or alone when there is none.
 * The viewer is a preview, and comes to the front.
 */
export function autoPlace(view: View, item: string, entry?: string): View | undefined {
  const target = largest(view.layout);
  return put(
    view,
    target === undefined ? undefined : { beside: target },
    viewerOf(item, entry, false),
  );
}

/**
 * Ordinary Open: with no front pane, as `autoPlace`; a preview in front is replaced in place, and a pinned pane
 * gets the new viewer to its right. `entry` is the entry of the item's Content the viewer starts on.
 */
export function open(view: View, item: string, entry?: string): View | undefined {
  const front = frontPane(view);
  if (front === undefined) return autoPlace(view, item, entry);
  const spot = view.viewers[front]?.pinned ? { beside: front } : { replacing: front };
  return put(view, spot, viewerOf(item, entry, false));
}

/** Open to the Side: a pinned viewer to the right of the front pane, whatever that pane is. */
export function openToSide(view: View, item: string, entry?: string): View | undefined {
  const front = frontPane(view) ?? largest(view.layout);
  return put(
    view,
    front === undefined ? undefined : { beside: front },
    viewerOf(item, entry, true),
  );
}

/**
 * Brings forward a viewer already showing `item` (and `entry`, when given), the front one if it is such: `view` with
 * that viewer in front, or `undefined` when there is none.
 */
export function reuse(view: View, item: string, entry?: string): View | undefined {
  const showing = leaves(view.layout).filter((id) => {
    const viewer = view.viewers[id];
    return viewer?.item === item && (entry === undefined || viewer.entry === entry);
  });
  const found =
    view.active !== undefined && showing.includes(view.active) ? view.active : showing[0];
  if (found === undefined) return undefined;
  return found === view.active ? view : { ...view, active: found };
}

/**
 * `view` without the panes `gone` names. The panes around them keep their sizes. If the front pane went, the first
 * one in layout order takes its place.
 */
export function close(view: View, gone: readonly number[]): View {
  const layout = prune(view.layout, (id) => !gone.includes(id));
  const placed = leaves(layout);
  const viewers: View["viewers"] = {};
  for (const id of placed) viewers[id] = view.viewers[id];
  const active =
    view.active !== undefined && placed.includes(view.active) ? view.active : placed[0];
  return { ...view, active, layout, viewers };
}

/** `view` with the pane `viewer` dropped on the `side` of `target`; it keeps its pin and comes to the front. */
export function moved(view: View, viewer: number, target: number, side: Side): View {
  if (!view.layout) return view;
  const layout = move(view.layout, viewer, target, side);
  return layout === view.layout ? view : { ...view, active: viewer, layout };
}

/** `view` with the pane `viewer` pinned or unpinned. */
export function pinned(view: View, viewer: number, pin: boolean): View {
  const was = view.viewers[viewer];
  if (!was || was.pinned === pin) return view;
  return { ...view, viewers: { ...view.viewers, [viewer]: { ...was, pinned: pin } } };
}

/** `view` with `viewer` in front. */
export function activated(view: View, viewer: number): View {
  return view.active === viewer || !(viewer in view.viewers) ? view : { ...view, active: viewer };
}

/**
 * The items that had a viewer in `before` and have none in `after`: the Doc still holds them open, and they need
 * a `closeViewer`. An item that keeps a pane, however many it lost, does not.
 */
export function leftWithout(before: View, after: View): string[] {
  const kept = new Set(Object.values(after.viewers).map((viewer) => viewer?.item));
  const items = Object.values(before.viewers).map((viewer) => viewer?.item);
  return [...new Set(items)].filter((item): item is string => !!item && !kept.has(item));
}
