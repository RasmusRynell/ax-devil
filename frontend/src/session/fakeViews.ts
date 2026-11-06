import type {
  EntryInfo,
  HiddenTarget,
  ItemState,
  Layout,
  View,
  ViewerView,
} from "../generated/api";
import { hides } from "./fakeRows";

const entriesOf = ({ resolution }: ItemState): EntryInfo[] => {
  if (resolution.status !== "Resolved") return [];
  const { content } = resolution;
  if (content.kind === "Playlist") return content.entries;
  return content.kind === "SeekableVideo" ? [content.entry] : [];
};

/** `layout` without the panes `keep` rejects: a split that loses a child becomes the other, which keeps its shares. */
function retain(layout: Layout, keep: (viewer: number) => boolean): Layout | undefined {
  if (layout.type === "pane") return keep(layout.viewer) ? layout : undefined;
  const first = retain(layout.first, keep);
  const second = retain(layout.second, keep);
  return first && second ? { ...layout, first, second } : (first ?? second);
}

/** The viewers of `layout`, in layout order. */
function viewersOf(layout: Layout): number[] {
  return layout.type === "pane"
    ? [layout.viewer]
    : [...viewersOf(layout.first), ...viewersOf(layout.second)];
}

/**
 * `view` cut down to the viewers the Doc has open and the layout places, each naming an entry: the one it asked for,
 * else the first of its item's Content that is not hidden, else the first. The backend also swaps an entry the
 * Content does not have; the fake leaves it, which lets a test name an entry of a Content it has not built.
 */
export function restrict(
  view: View,
  items: readonly ItemState[],
  hidden: readonly HiddenTarget[],
): View {
  const open = new Map(items.filter((item) => item.open).map((item) => [item.id, item]));
  const layout =
    view.layout &&
    retain(view.layout, (id) => {
      const viewer = view.viewers[id];
      return !!viewer && open.has(viewer.item);
    });
  const viewers: View["viewers"] = {};
  for (const id of layout ? viewersOf(layout) : []) {
    const viewer = view.viewers[id];
    const item = viewer && open.get(viewer.item);
    if (!viewer || !item) continue;
    const entries = entriesOf(item);
    const fallback = entries.find(({ id: entryId }) => !hides(hidden, entryId)) ?? entries[0];
    viewers[id] = { ...viewer, entry: viewer.entry ?? fallback?.id };
  }
  return {
    sidebar: view.sidebar,
    active: view.active !== undefined && viewers[view.active] ? view.active : undefined,
    viewers,
    ...(layout && { layout }),
  };
}

/**
 * The part of a view that shows `items`, one viewer each side by side, numbered from 1 with the first in front. An
 * item given with settings has them as its viewer's.
 */
export function viewing(
  ...items: (string | readonly [item: string, viewer: Partial<ViewerView>])[]
): Pick<View, "active" | "viewers" | "layout"> {
  const viewers: View["viewers"] = {};
  let layout: Layout | undefined;
  items.forEach((shown, index) => {
    const [item, rest] = typeof shown === "string" ? [shown, {}] : shown;
    const viewer = index + 1;
    viewers[viewer] = { item, pinned: false, ...rest };
    const pane: Layout = { type: "pane", viewer };
    layout = layout
      ? {
          type: "split",
          axis: "row",
          share: Math.round((10_000 * index) / (index + 1)),
          first: layout,
          second: pane,
        }
      : pane;
  });
  return { active: items.length > 0 ? 1 : undefined, viewers, layout };
}
