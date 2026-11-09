// Generated from sessions::session::views by make; do not edit.

import type { ClockIntent } from "./playback.authority";
import type { Layout } from "./sessions.session.layout";

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
layout?: Layout, };

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
entry?: string, };
