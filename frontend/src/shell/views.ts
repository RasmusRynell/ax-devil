import type { IconName } from "../kit/icons";

export type ViewId = "workspace" | "live" | "offline" | "bookmarks" | "plugins";

export interface ActivityItem {
  id: string;
  label: string;
  icon: IconName;
  /** A sidebar view, or a command that opens something else. */
  opens: "view" | "command";
}

/** The activity bar: the top group, then the bottom group. */
export const ACTIVITY: { top: readonly ActivityItem[]; bottom: readonly ActivityItem[] } = {
  top: [
    { id: "workspace", label: "Workspace", icon: "layout-list", opens: "view" },
    { id: "live", label: "Live", icon: "video", opens: "view" },
    { id: "offline", label: "Offline", icon: "square-play", opens: "view" },
    { id: "bookmarks", label: "Bookmarks", icon: "bookmark", opens: "view" },
  ],
  bottom: [
    { id: "plugins", label: "Plugins", icon: "plug", opens: "view" },
    { id: "render-catalogs", label: "Render Catalogs", icon: "palette", opens: "command" },
    { id: "settings", label: "Settings", icon: "settings", opens: "command" },
  ],
};

/** The search field placeholder of the views that have one. */
export const SEARCH: Partial<Record<string, string>> = {
  workspace: "Search items",
  live: "Search cameras",
  offline: "Search files",
};

export const VIEW_TITLES: Record<ViewId, string> = Object.fromEntries(
  [...ACTIVITY.top, ...ACTIVITY.bottom]
    .filter((item) => item.opens === "view")
    .map((item) => [item.id, item.label]),
) as Record<ViewId, string>;
