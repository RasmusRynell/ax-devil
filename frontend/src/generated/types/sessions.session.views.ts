// Generated from sessions::session::views by make; do not edit.

import type { ClockIntent } from "./playback.authority";
import type { Layout } from "./sessions.session.layout";

/**
 * One of the [`Overlays`] toggles.
 */
export type Overlay = "objectDetails" | "laneNames";

/**
 * The two overlays a window can turn off: object details (hover, chip and selection) and lane names.
 */
export type Overlays = { 
/**
 * Whether hovering or clicking an object shows its details.
 */
objectDetails: boolean, 
/**
 * Whether a lane's chip shows its name.
 */
laneNames: boolean, };

/**
 * What the sidebar shows.
 */
export type Sidebar = { 
/**
 * Which view.
 */
view: SidebarView, 
/**
 * Whether the sidebar is open.
 */
open: boolean, };

/**
 * What the sidebar shows; part of the view a session shares.
 */
export type SidebarView = "workspace" | "live" | "offline" | "bookmarks" | "plugins" | "sessions";

/**
 * What a window shows of the Doc. Items the Doc does not have open are dropped by the backend, with their viewers.
 */
export type View = { 
/**
 * The sidebar.
 */
sidebar: Sidebar, 
/**
 * The viewer in front; absent shows the welcome page.
 */
active?: number, 
/**
 * What each open viewer shows. A viewer the layout does not place is dropped by the backend.
 */
viewers: { [key in number]?: ViewerView }, 
/**
 * Where the viewers are; absent while there are none.
 */
layout?: Layout, 
/**
 * The overlays every viewer of the view draws. Only an overlay command sets them: a view sent whole keeps the
 * ones stored, so a stale copy never reverts another seat's toggle.
 */
overlays: Overlays, };

/**
 * What one open viewer shows.
 */
export type ViewerView = { 
/**
 * The item the viewer shows. Another viewer may show the same one.
 */
item: string, 
/**
 * Whether opening other content leaves this viewer where it is.
 */
pinned: boolean, 
/**
 * What this window's clock for the viewer is doing; absent until it has one.
 */
clock?: ClockIntent, 
/**
 * The entry of the item's Content the viewer shows. The backend keeps it naming an entry of the Content as it is
 * resolved now; absent while the item is not resolved.
 */
entry?: string, 
/**
 * The part of the video the viewer shows; absent shows the whole frame. Only a viewport change sets it: a view
 * sent whole keeps the one stored, so a stale copy never reverts another seat's zoom.
 */
viewport?: Viewport, };

/**
 * The part of a video a window shows: a rectangle in video coordinates, each of `x`, `y`, `w` and `h` a fraction of
 * the frame. A window fits it inside its own video area, so one of another shape shows at least this region.
 */
export type Viewport = { 
/**
 * Left edge.
 */
x: number, 
/**
 * Top edge.
 */
y: number, 
/**
 * Width.
 */
w: number, 
/**
 * Height.
 */
h: number, };
