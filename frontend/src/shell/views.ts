import type { SidebarView } from "../generated/api";
import { ICONS, type IconName } from "../kit/icons";
import type { PluginView } from "../plugins/bus";
import { viewName } from "../plugins/usePlugins";

/** The views the app itself has, which are listed in the activity bar and have a title. */
export type BuiltinView = Exclude<SidebarView, { plugin: unknown }>;

export interface ActivityItem {
  id: string;
  label: string;
  icon: IconName;
  /** A sidebar view, or a command that opens something else. */
  opens: "view" | "command";
  /** A plugin's view, which `id` names as `<plugin id>.<view id>`. */
  plugin?: true;
}

/** What identifies the view in the sidebar: its activity item's id. */
export const viewId = (view: SidebarView): string =>
  typeof view === "string" ? view : view.plugin.view;

/** The sidebar view an activity item opens. */
export const viewOf = (item: ActivityItem): SidebarView =>
  item.plugin ? { plugin: { view: item.id } } : (item.id as BuiltinView);

/** The activity item of a plugin's sidebar view. */
export const pluginItem = (view: PluginView): ActivityItem => ({
  id: viewName(view),
  label: view.view.title,
  icon: view.view.icon && view.view.icon in ICONS ? (view.view.icon as IconName) : "plug",
  opens: "view",
  plugin: true,
});

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
    { id: "sessions", label: "Sessions", icon: "users", opens: "view" },
    { id: "settings", label: "Settings", icon: "settings", opens: "command" },
  ],
};

/** The search field placeholder of the views that have one. */
export const SEARCH: Partial<Record<string, string>> = {
  workspace: "Search items",
  offline: "Search files",
};

export const VIEW_TITLES: Record<BuiltinView, string> = Object.fromEntries(
  [...ACTIVITY.top, ...ACTIVITY.bottom]
    .filter((item) => item.opens === "view")
    .map((item) => [item.id, item.label]),
) as Record<BuiltinView, string>;
