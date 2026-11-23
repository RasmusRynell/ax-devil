import type { Layout, View, ViewerView } from "../generated/api";
import { EMPTY_VIEW } from "./views";

/**
 * The part of a view that shows `items`, one viewer each side by side, numbered from 1 with the first in front. An
 * item given with settings has them as its viewer's.
 */
export function viewing(
  ...items: (string | readonly [item: string, viewer: Partial<ViewerView>])[]
): Pick<View, "active" | "viewers" | "layout" | "overlays"> {
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
  return {
    active: items.length > 0 ? 1 : undefined,
    viewers,
    layout,
    overlays: EMPTY_VIEW.overlays,
  };
}
