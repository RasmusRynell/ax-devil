import { useMemo, type CSSProperties } from "react";
import { BookmarksView } from "../bookmarks/BookmarksView";
import { countOf } from "../bookmarks/groups";
import type { SidebarView } from "../generated/api";
import type { AddSeed } from "../live/cameras";
import { ViewPanel } from "../kit/ViewPanel";
import { useCan, useDoc, useRenameWorkspace, useView } from "../session/session";
import { PluginsView } from "../plugins/PluginsView";
import { SidebarFrame } from "../plugins/PluginFrame";
import { usePluginViews, viewName } from "../plugins/usePlugins";
import { PanelDivider } from "../tools/PanelDivider";
import { useKept } from "../kit/keptState";
import { LiveView } from "./LiveView";
import { useStreaming } from "../live/cameras";
import { OfflineAdd } from "./OfflineAdd";
import { SessionsView } from "./SessionsView";
import { StartPanel } from "./StartPanel";
import { listedCount, treeOf, type Order, type SectionId } from "./treeModel";
import { WorkspaceTree } from "./WorkspaceTree";
import { UNTITLED } from "./useWindowTitle";
import { VIEW_TITLES, type BuiltinView } from "./views";

/** The views that list the workspace's items: the sections each lists, the orders it offers, its search placeholder and its empty message. */
const LISTINGS: Partial<
  Record<
    BuiltinView,
    { listed: readonly SectionId[]; orders: readonly Order[]; search: string; empty: string }
  >
> = {
  workspace: {
    listed: ["live", "videos", "playlists", "other"],
    orders: ["workspace", "name"],
    search: "Search items",
    empty: "No items",
  },
  offline: {
    listed: ["videos", "playlists"],
    orders: ["workspace", "name", "location"],
    search: "Search files",
    empty: "No videos or playlists",
  },
};

/** The views that list the workspace's items. */
export const LISTED_VIEWS = Object.keys(LISTINGS) as BuiltinView[];

/**
 * The sidebar: a title and the view's content; the Live view has its own toolbar and says in the header how many
 * streams run, the Offline view how many videos and playlists it lists. The Workspace view is titled with the workspace's name,
 * `Untitled` without one, and a dot while it has unsaved changes; while it has no items it shows the start panel
 * instead of the list. Its right edge drags: between what the view needs
 * and half the space beside the activity bar, remembered while the window lives, and a drag to nothing hides it.
 */
export function Sidebar({
  view: asked,
  onRun,
  onAddLive,
}: {
  view: SidebarView;
  onRun: (id: string) => void;
  onAddLive: (seed: AddSeed) => void;
}) {
  // A plugin view the listing no longer has (its plugin was removed or failed) shows the Plugins view.
  const plugin = usePluginViews("sidebar").find(
    (candidate) => typeof asked !== "string" && viewName(candidate) === asked.plugin.view,
  );
  const view: BuiltinView = typeof asked === "string" ? asked : "plugins";
  const listing = LISTINGS[view];
  // The width the user dragged to, kept while the sidebar is hidden and shown again.
  const [width, setWidth] = useKept<number | null>("sidebar:width", null);
  const [, setShell] = useView("sidebar");
  const streaming = useStreaming();
  const workspace = useDoc()?.workspace;
  const offline = useMemo(
    () =>
      workspace && view === "offline" && listing
        ? listedCount(treeOf(workspace), listing.listed)
        : 0,
    [workspace, view, listing],
  );
  const marked = useMemo(
    () => (workspace && view === "bookmarks" ? countOf(workspace.bookmarks) : 0),
    [workspace, view],
  );
  const named = view === "workspace" ? workspace : undefined;
  // An empty workspace shows the start panel alone, which scrolls with the recent workspaces; items replace it.
  const starting = view === "workspace" && (workspace?.items.length ?? 0) === 0;
  const rename = useRenameWorkspace();
  const canEdit = useCan("content");
  return (
    <aside
      aria-label="Sidebar"
      data-cursor-region="sidebar"
      style={width === null ? undefined : ({ "--sidebar-width": `${width}px` } as CSSProperties)}
      className="relative flex shrink-0 flex-col max-sm:min-h-0 max-sm:w-full max-sm:flex-1 sm:w-[var(--sidebar-width,var(--spacing-sidebar))] sm:max-w-[calc((100%-var(--spacing-activity))/2)] sm:min-w-sidebar-least sm:border-r sm:border-line"
    >
      <PanelDivider
        label="Resize sidebar"
        dock="left"
        className="max-sm:hidden"
        onResize={setWidth}
        onClose={() => setShell((shown) => ({ ...shown, open: false }))}
      />
      <ViewPanel
        key={named?.epoch}
        title={named ? (named.name ?? UNTITLED) : (plugin?.view.title ?? VIEW_TITLES[view])}
        tooltip={named ? (named.file ?? undefined) : undefined}
        edit={
          named && canEdit
            ? { label: "Workspace name", onEdit: rename.edit, onDone: rename.done }
            : undefined
        }
        cursorId="sidebar:title"
        action={
          view === "live" && streaming > 0 ? (
            <span className="text-small text-muted">{`${streaming} streaming`}</span>
          ) : offline + marked > 0 ? (
            <span className="text-small text-muted">{offline + marked}</span>
          ) : named?.modified ? (
            <span
              role="img"
              aria-label="Unsaved changes"
              title="Unsaved changes"
              className="size-m shrink-0 rounded-full bg-accent"
            />
          ) : undefined
        }
      >
        {plugin ? (
          <SidebarFrame view={plugin} />
        ) : view === "live" ? (
          <LiveView onAdd={onAddLive} />
        ) : (
          <div
            className={`flex flex-col gap-m px-s pb-s ${listing || view === "bookmarks" ? "min-h-0 flex-1" : ""}`}
          >
            {starting && (
              <div className="min-h-0 flex-1 overflow-y-auto">
                <StartPanel onRun={onRun} />
              </div>
            )}
            {listing && !starting && <WorkspaceTree key={view} view={view} {...listing} />}
            {view === "offline" && <OfflineAdd onRun={onRun} />}
            {view === "bookmarks" && <BookmarksView />}
            {view === "sessions" && <SessionsView />}
            {view === "plugins" && <PluginsView />}
          </div>
        )}
      </ViewPanel>
    </aside>
  );
}
